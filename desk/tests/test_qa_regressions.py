"""Regressions from the PR #1 QA pass. One test per reported defect."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from desk.analytics import dashboard_data
from desk.models import (
    ActivityEvent,
    Category,
    Customer,
    Membership,
    Notification,
    SatisfactionResponse,
    Ticket,
    Workspace,
)
from desk.services import update_ticket


class QARegressionTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(
            name="QA", slug="qa", timezone="Asia/Tokyo", next_ticket_number=1002
        )
        self.user = User.objects.create_user(
            "admin@example.com", email="admin@example.com", first_name="Ada", last_name="Admin"
        )
        self.admin = Membership.objects.create(
            workspace=self.workspace, user=self.user, role="admin"
        )
        agent_user = User.objects.create_user(
            "agent@example.com", email="agent@example.com", first_name="Gus"
        )
        self.agent = Membership.objects.create(
            workspace=self.workspace, user=agent_user, role="agent"
        )
        self.customer = Customer.objects.create(
            workspace=self.workspace, name="Olivia Rhodes", email="olivia@example.com"
        )
        self.ticket = Ticket.objects.create(
            workspace=self.workspace,
            customer=self.customer,
            number=1001,
            subject="An invoice question",
        )
        self.client.force_login(self.user)

    def update(self, ticket, member=None, **changes):
        data = {
            "status": ticket.status,
            "priority": ticket.priority,
            "assignee": ticket.assignee,
            "category": ticket.category,
            "revision": ticket.revision,
            **changes,
        }
        return update_ticket(member or self.admin, ticket.pk, data)

    # 1, 2 and the Postgres NUL case: odd filter values return results, not 500.
    def test_unusual_filter_values_do_not_crash(self):
        for query in [
            "q=%C2%B2",
            "assignee=%C2%B2",
            "assignee=99999999999999999999",
            "category=99999999999999999999",
            "q=99999999999999999999",
            "q=%00",
        ]:
            self.assertEqual(self.client.get(f"/tickets/?{query}").status_code, 200, query)
            self.assertEqual(self.client.get(f"/api/v1/tickets/?{query}").status_code, 200, query)
        self.assertEqual(self.client.get("/customers/?q=%00").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/customers/?q=%00").status_code, 200)

    # 3: status cards show the current backlog; resolved, CSAT and team use resolved_at.
    def test_dashboard_counts_backlog_and_resolutions_in_period(self):
        old = timezone.now() - timedelta(days=40)
        Ticket.objects.filter(pk=self.ticket.pk).update(created_at=old)
        resolved = Ticket.objects.create(
            workspace=self.workspace,
            customer=self.customer,
            number=1002,
            subject="Old, resolved today",
            status="RESOLVED",
            assignee=self.agent,
            resolved_at=timezone.now(),
            first_response_at=old,
        )
        Ticket.objects.filter(pk=resolved.pk).update(created_at=old)
        SatisfactionResponse.objects.create(ticket=resolved, score=5, submitted_at=timezone.now())

        data = dashboard_data(self.workspace, days=30)
        metrics = data["metrics"]
        self.assertEqual(metrics["open"], 1)
        self.assertEqual(metrics["resolved"], 1)
        self.assertEqual(metrics["csat"], 100)
        self.assertEqual(metrics["csat_count"], 1)
        status = {item["key"]: item["value"] for item in data["status_counts"]}
        self.assertEqual(status["OPEN"], Ticket.objects.filter(status="OPEN").count())
        team = {row["member"].pk: row for row in data["team_performance"]}
        self.assertEqual(team[self.agent.pk]["resolved"], 1)

    # 13b: a zero-minute average is data, not "No data".
    def test_zero_minute_response_time_is_reported(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(
            first_response_at=self.ticket.created_at, assignee=self.agent
        )
        data = dashboard_data(self.workspace, days=30)
        self.assertEqual(data["metrics"]["response_time"], 0)
        team = {row["member"].pk: row for row in data["team_performance"]}
        self.assertEqual(team[self.agent.pk]["response_time"], 0)

    # 4: a reply conflict keeps the typed text and offers the current revision.
    def test_reply_conflict_rerenders_with_current_revision(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(revision=3)
        response = self.client.post(
            reverse("ticket_reply", args=[self.ticket.pk]),
            {"body": "My careful reply", "revision": 2},
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "My careful reply", status_code=409)
        self.assertNotContains(response, 'name="revision" value="2"', status_code=409)
        retry = self.client.post(
            reverse("ticket_reply", args=[self.ticket.pk]),
            {"body": "My careful reply", "revision": 3},
        )
        self.assertEqual(retry.status_code, 302)

    # 4: a metadata conflict reloads current values instead of resubmitting stale ones.
    def test_update_conflict_reloads_current_ticket(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(revision=3, priority="HIGH")
        response = self.client.post(
            reverse("ticket_update", args=[self.ticket.pk]),
            {"status": "OPEN", "priority": "LOW", "revision": 2},
            follow=True,
        )
        self.assertRedirects(response, reverse("ticket_detail", args=[self.ticket.pk]))
        self.assertContains(response, "This ticket has changed")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.priority, "HIGH")

    # 5: a user without an active membership is signed out, not stuck on 403.
    def test_deactivated_user_is_signed_out(self):
        Membership.objects.filter(pk=self.admin.pk).update(is_active=False)
        response = self.client.get(reverse("dashboard"), follow=True)
        self.assertRedirects(response, reverse("login"))
        self.assertContains(response, "no longer has access")
        self.assertNotIn("_auth_user_id", self.client.session)

    # 6: the API honours the documented category filter.
    def test_api_filters_by_category(self):
        billing = Category.objects.create(workspace=self.workspace, name="Billing")
        Ticket.objects.create(
            workspace=self.workspace,
            customer=self.customer,
            number=1002,
            subject="Card declined",
            category=billing,
        )
        data = self.client.get(f"/api/v1/tickets/?category={billing.pk}").json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["category"], billing.pk)

    # 7: saving unchanged values has no side effects.
    def test_unchanged_save_keeps_revision_and_records_nothing(self):
        events = ActivityEvent.objects.count()
        result = self.update(self.ticket)
        self.assertEqual(result.revision, self.ticket.revision)
        self.assertEqual(ActivityEvent.objects.count(), events)

    # 8: the agent who loses a ticket hears about it.
    def test_previous_assignee_is_notified_on_reassignment(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(assignee=self.agent)
        self.ticket.refresh_from_db()
        self.update(self.ticket, assignee=None)
        self.assertTrue(Notification.objects.filter(recipient=self.agent).exists())

    # 9: "New ticket" from a customer profile preselects that customer.
    def test_new_ticket_prefills_customer(self):
        response = self.client.get(reverse("ticket_create") + f"?customer={self.customer.pk}")
        self.assertContains(response, f'<option value="{self.customer.pk}" selected>')

    # 10: ticket-number search ignores case.
    def test_ticket_number_search_ignores_case(self):
        for q in ["sf-1001", "SF-1001", "#1001", "1001"]:
            self.assertEqual(self.client.get(f"/api/v1/tickets/?q={q}").json()["count"], 1, q)

    # 11: a workspace timezone does not leak past its request.
    def test_workspace_timezone_is_reset_after_request(self):
        self.client.get(reverse("dashboard"))
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")

    # 12: notifications name the actor and the changed fields.
    def test_notification_names_actor_and_changes(self):
        self.update(self.ticket, status="PENDING", assignee=self.agent)
        self.client.force_login(self.agent.user)
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, "Ada Admin")
        self.assertContains(response, "status")
        self.assertContains(response, "assignee")

    # 13a: domain errors on team updates come back as a page message.
    def test_last_admin_guard_redirects_with_message(self):
        response = self.client.post(
            reverse("member_update", args=[self.admin.pk]),
            {"role": "agent", "is_active": "on"},
            follow=True,
        )
        self.assertRedirects(response, reverse("team"))
        self.assertContains(response, "at least one active administrator")

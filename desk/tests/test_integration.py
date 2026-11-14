"""Checks at the browser, API, and deployment configuration boundaries."""

import os
import subprocess
import sys
from unittest.mock import patch
from http.client import BadStatusLine

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from desk import ai
from desk.models import (
    ActivityEvent,
    Customer,
    Membership,
    Message,
    Notification,
    Ticket,
    Workspace,
)


class IntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "integration@example.com",
            email="integration@example.com",
            password="A-very-good-password!2026",
        )
        self.workspace = Workspace.objects.create(
            name="Integration", slug="integration", next_ticket_number=1002
        )
        self.member = Membership.objects.create(
            workspace=self.workspace, user=self.user, role="admin"
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

    def test_original_customer_message_is_not_rendered_twice(self):
        self.ticket.description = "One unique request body."
        self.ticket.save()
        Message.objects.create(
            ticket=self.ticket,
            customer=self.customer,
            kind="CUSTOMER",
            body="Hi team, One unique request body. Thanks!",
        )
        response = self.client.get(reverse("ticket_detail", args=[self.ticket.pk]))
        self.assertEqual(response.content.count(b"One unique request body."), 1)

    def test_delete_confirmation_submits_the_revision_and_displays_conflicts(self):
        url = reverse("ticket_delete", args=[self.ticket.pk])
        response = self.client.get(url)
        self.assertContains(response, 'name="revision"')
        self.assertContains(response, 'value="1"')
        Ticket.objects.filter(pk=self.ticket.pk).update(revision=2)
        response = self.client.post(url, {"revision": 1})
        self.assertContains(response, "This ticket has changed", status_code=409)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.deleted_at)
        response = self.client.post(url, {"revision": 2})
        self.assertRedirects(response, reverse("ticket_list"))
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.deleted_at)

    def test_notification_count_changes_after_marking_a_notification_read(self):
        event = ActivityEvent.objects.create(
            workspace=self.workspace,
            kind="ticket.updated",
            description="Updated ticket",
            ticket=self.ticket,
        )
        item = Notification.objects.create(recipient=self.member, event=event)
        self.assertEqual(self.client.get("/api/v1/me/").json().get("unread_count"), 1)
        response = self.client.post(f"/api/v1/notifications/{item.pk}/read/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/me/").json()["unread_count"], 0)

    def test_api_rejects_missing_csrf_and_accepts_a_issued_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        data = {
            "customer": self.customer.pk,
            "subject": "CSRF verified",
            "status": "OPEN",
            "priority": "MEDIUM",
        }
        response = client.post("/api/v1/tickets/", data, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        token = client.get("/api/v1/csrf/").json()["csrfToken"]
        response = client.post(
            "/api/v1/tickets/", data, content_type="application/json", HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(response.status_code, 201, response.content)

    @override_settings(DEMO_MODE=False, AI_PROVIDER="demo")
    def test_demo_ai_is_labeled_even_in_a_production_workspace(self):
        self.assertContains(self.client.get(reverse("settings")), "Demo AI")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_email_contains_a_working_reset_link(self):
        response = self.client.post(reverse("password_reset"), {"email": self.user.email})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        link = next(
            line.strip() for line in mail.outbox[0].body.splitlines() if "/password/reset/" in line
        )
        self.client.logout()
        response = self.client.get(link, follow=True)
        self.assertContains(response, "new password", html=False)

    @override_settings(
        AI_PROVIDER="openai-compatible",
        AI_BASE_URL="https://provider.example/v1",
        AI_MODEL="support-model",
        AI_API_KEY="fixture-key",
    )
    def test_malformed_provider_http_is_a_safe_failure(self):
        with patch(
            "urllib.request.OpenerDirector.open",
            side_effect=BadStatusLine("provider private details"),
        ):
            try:
                ai.analyze_ticket(self.ticket, "reply", self.user)
            except Exception as error:
                self.assertIsInstance(
                    error, ai.AIError, "Transport errors must preserve the safe AI contract"
                )
                self.assertNotIn("private details", str(error))
            else:
                self.fail("Malformed HTTP must be rejected")


class DatabaseConfigurationTests(SimpleTestCase):
    def test_ambient_database_url_cannot_select_an_unrelated_database(self):
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "DEBUG": "true",
            "DATABASE_URL": "postgresql:///unrelated-runtime-fixture",
        }
        result = subprocess.check_output(
            [
                sys.executable,
                "-c",
                'from supportflow.settings import DATABASES; print(DATABASES["default"]["ENGINE"])',
            ],
            env=environment,
            text=True,
        )
        self.assertEqual(result.strip(), "django.db.backends.sqlite3")

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from desk.analytics import dashboard_data
from desk.models import Customer, Membership, Ticket, Workspace
from desk.services import (
    DomainError,
    accept_invitation,
    create_invitation,
    update_member,
)


class ReleaseRegressionTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Regression", slug="regression")
        self.owner = User.objects.create_user("owner@example.com", email="owner@example.com")
        self.admin = Membership.objects.create(
            workspace=self.workspace, user=self.owner, role="admin"
        )

    def test_deactivation_revokes_old_invites_but_allows_fresh_invitation(self):
        user = User.objects.create_user("agent@example.com", email="agent@example.com")
        first = create_invitation(self.admin, {"email": user.email, "role": "agent"})
        old = create_invitation(self.admin, {"email": user.email, "role": "admin"})
        accept_invitation(first, user)
        member = Membership.objects.get(workspace=self.workspace, user=user)
        update_member(self.admin, member.pk, {"role": "agent", "is_active": False})

        with self.assertRaises(DomainError) as rejected:
            accept_invitation(old, user)
        self.assertEqual(rejected.exception.code, "invalid_invitation")
        member.refresh_from_db()
        self.assertFalse(member.is_active)

        fresh = create_invitation(self.admin, {"email": user.email, "role": "agent"})
        accept_invitation(fresh, user)
        member.refresh_from_db()
        self.assertTrue(member.is_active)
        self.assertEqual(member.role, "agent")

    def test_resolved_chart_counts_old_tickets_resolved_during_period(self):
        customer = Customer.objects.create(
            workspace=self.workspace, name="Customer", email="customer@example.com"
        )
        ticket = Ticket.objects.create(
            workspace=self.workspace, customer=customer, number=1001,
            subject="Old request", status="RESOLVED", resolved_at=timezone.now(),
        )
        Ticket.objects.filter(pk=ticket.pk).update(created_at=timezone.now() - timedelta(days=40))
        data = dashboard_data(self.workspace, days=30)
        self.assertEqual(data["metrics"]["total"], 0)
        self.assertEqual(sum(point["created"] for point in data["trends"]), 0)
        self.assertEqual(sum(point["resolved"] for point in data["trends"]), 1)

        # Deleted records and a different workspace never leak into this chart.
        Ticket.objects.filter(pk=ticket.pk).update(deleted_at=timezone.now())
        other = Workspace.objects.create(name="Other", slug="other")
        foreign = Customer.objects.create(workspace=other, name="Private", email="p@example.com")
        Ticket.objects.create(
            workspace=other, customer=foreign, number=1001, subject="Private request",
            status="RESOLVED", resolved_at=timezone.now(),
        )
        data = dashboard_data(self.workspace, days=30)
        self.assertEqual(sum(point["resolved"] for point in data["trends"]), 0)

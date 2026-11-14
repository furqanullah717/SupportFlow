from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase


class WorkspaceIntegrityTests(TestCase):
    def test_ticket_rejects_a_customer_from_another_workspace(self):
        self.assertTrue(
            apps.all_models["desk"].get("ticket"), "Tickets must enforce workspace isolation"
        )
        from desk.models import Workspace, Customer, Ticket

        first = Workspace.objects.create(name="First", slug="first")
        second = Workspace.objects.create(name="Second", slug="second")
        customer = Customer.objects.create(
            workspace=second, name="Someone", email="someone@example.com"
        )
        ticket = Ticket(workspace=first, customer=customer, number=1, subject="Access")
        with self.assertRaises(ValidationError):
            ticket.full_clean()

    def test_assignee_must_be_active_and_in_workspace(self):
        from desk.models import Workspace, Customer, Membership, Ticket

        first = Workspace.objects.create(name="First", slug="first")
        second = Workspace.objects.create(name="Second", slug="second")
        user = User.objects.create_user(username="agent")
        member = Membership.objects.create(workspace=second, user=user)
        customer = Customer.objects.create(
            workspace=first, name="Customer", email="customer@example.com"
        )
        ticket = Ticket(
            workspace=first, customer=customer, assignee=member, number=1, subject="Help"
        )
        with self.assertRaises(ValidationError):
            ticket.full_clean()
        member.workspace = first
        member.is_active = False
        member.save()
        with self.assertRaises(ValidationError):
            ticket.full_clean()

    def test_ticket_number_and_status_are_enforced_by_database(self):
        from desk.models import Workspace, Customer, Ticket

        workspace = Workspace.objects.create(name="First", slug="first")
        customer = Customer.objects.create(
            workspace=workspace, name="Customer", email="customer@example.com"
        )
        Ticket.objects.create(workspace=workspace, customer=customer, number=1, subject="Help")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.create(
                workspace=workspace, customer=customer, number=1, subject="Duplicate"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.create(
                workspace=workspace,
                customer=customer,
                number=2,
                subject="Invalid",
                status="INVALID",
            )

    def test_satisfaction_rejects_out_of_range_scores(self):
        from desk.models import Workspace, Customer, Ticket, SatisfactionResponse

        workspace = Workspace.objects.create(name="First", slug="first")
        customer = Customer.objects.create(
            workspace=workspace, name="Customer", email="customer@example.com"
        )
        ticket = Ticket.objects.create(
            workspace=workspace, customer=customer, number=1, subject="Help"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SatisfactionResponse.objects.create(ticket=ticket, score=6)


class DemoSeedTests(TestCase):
    def test_demo_seed_is_idempotent_and_has_realistic_metrics(self):
        from django.core.management import call_command, get_commands

        self.assertIn(
            "seed_demo", get_commands(), "An explicit repeatable demo seed must be available"
        )
        from desk.models import (
            Workspace,
            Ticket,
            Message,
            Customer,
            Membership,
            SatisfactionResponse,
        )

        call_command("seed_demo", verbosity=0)
        workspace = Workspace.objects.get(slug="supportflow-demo")
        before = (
            Ticket.objects.count(),
            Message.objects.count(),
            Customer.objects.count(),
            Membership.objects.count(),
        )
        call_command("seed_demo", verbosity=0)
        self.assertEqual(
            before,
            (
                Ticket.objects.count(),
                Message.objects.count(),
                Customer.objects.count(),
                Membership.objects.count(),
            ),
        )
        self.assertGreaterEqual(workspace.tickets.count(), 100)
        self.assertEqual(workspace.customers.count(), 25)
        self.assertEqual(workspace.memberships.count(), 6)
        self.assertEqual(
            set(workspace.tickets.values_list("status", flat=True)),
            {"OPEN", "IN_PROGRESS", "PENDING", "RESOLVED"},
        )
        self.assertTrue(SatisfactionResponse.objects.filter(submitted_at__isnull=False).exists())
        for ticket in workspace.tickets.filter(first_response_at__isnull=False):
            self.assertGreater(ticket.first_response_at, ticket.created_at)
            self.assertLessEqual(ticket.first_response_at, ticket.updated_at)
        self.assertTrue(
            User.objects.get(username="admin@supportflow.demo").check_password("SupportFlow!2026")
        )

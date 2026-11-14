import io
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from desk import ai
from desk.models import AIExecution, Category, Customer, Membership, Message, Ticket, Workspace


@override_settings(AI_PROVIDER="demo")
class AIAssistanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("agent@example.com")
        self.workspace = Workspace.objects.create(name="Acme", slug="acme")
        Membership.objects.create(workspace=self.workspace, user=self.user)
        self.customer = Customer.objects.create(
            workspace=self.workspace, name="Olivia Rhodes", email="olivia@example.com"
        )
        Category.objects.create(workspace=self.workspace, name="Billing")
        Category.objects.create(workspace=self.workspace, name="Technical issue")
        self.ticket = Ticket.objects.create(
            workspace=self.workspace,
            number=1001,
            customer=self.customer,
            subject="Duplicate invoice charge",
            description="I was charged twice.",
            revision=4,
        )
        Message.objects.create(
            ticket=self.ticket, kind="CUSTOMER", body="Please review the duplicate payment."
        )
        Message.objects.create(
            ticket=self.ticket, kind="AGENT", internal=True, body="PRIVATE-INTERNAL-SECRET"
        )

    def ready(self):
        self.assertTrue(
            callable(getattr(ai, "analyze_ticket", None)),
            "Ticket AI must generate reviewed suggestions through a provider boundary",
        )

    def analyze(self, operation, user=None):
        self.ready()
        return ai.analyze_ticket(self.ticket, operation, user or self.user)

    def test_demo_reply_is_labeled_logged_and_does_not_change_ticket(self):
        output = self.analyze("reply")
        self.assertTrue(output["demo"])
        self.assertEqual(output["provider"], "demo")
        self.assertEqual(output["source_revision"], 4)
        self.assertIn("Olivia", output["result"]["text"])
        self.assertNotIn("PRIVATE-INTERNAL-SECRET", output["result"]["text"])
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.revision, 4)
        self.assertEqual(self.ticket.messages.count(), 2)
        execution = AIExecution.objects.get()
        self.assertEqual((execution.status, execution.source_revision), ("success", 4))

    def test_demo_summary_uses_public_conversation(self):
        output = self.analyze("summary")
        self.assertIn("duplicate payment", output["result"]["text"])
        self.assertNotIn("PRIVATE-INTERNAL-SECRET", output["result"]["text"])

    def test_demo_classification_uses_workspace_categories(self):
        result = self.analyze("classify")["result"]
        self.assertEqual(result["category"], "Billing")
        self.assertIn(result["priority"], Ticket.Priority.values)
        self.assertTrue(result["rationale"])

    def test_nonmember_cannot_send_ticket_data_to_a_provider(self):
        self.ready()
        outsider = User.objects.create_user("outsider@example.com")
        with self.assertRaises(PermissionDenied):
            ai.analyze_ticket(self.ticket, "reply", outsider)
        self.assertFalse(AIExecution.objects.exists())

    @override_settings(
        AI_PROVIDER="openai-compatible",
        AI_BASE_URL="https://provider.example/v1",
        AI_MODEL="support-model",
        AI_API_KEY="api-secret",
    )
    def test_external_payload_excludes_internal_notes_and_validates_response(self):
        self.ready()
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"text": "Thanks for the details. Can you share the invoice number?"}
                        )
                    }
                }
            ]
        }
        with patch(
            "urllib.request.OpenerDirector.open",
            return_value=io.BytesIO(json.dumps(response).encode()),
        ) as transport:
            output = self.analyze("reply")
        request = transport.call_args.args[0]
        self.assertNotIn("PRIVATE-INTERNAL-SECRET", request.data.decode())
        self.assertNotIn("api-secret", request.data.decode())
        self.assertEqual(transport.call_args.kwargs["timeout"], 20)
        self.assertFalse(output["demo"])
        self.assertEqual(output["model"], "support-model")
        self.assertIn("invoice number", output["result"]["text"])

    @override_settings(
        AI_PROVIDER="openai-compatible",
        AI_BASE_URL="https://provider.example/v1",
        AI_MODEL="support-model",
        AI_API_KEY="api-secret",
    )
    def test_external_timeout_is_safe_and_logs_failure(self):
        self.ready()
        with patch(
            "urllib.request.OpenerDirector.open",
            side_effect=TimeoutError("api-secret provider details"),
        ):
            with self.assertRaises(ai.AIError) as failure:
                self.analyze("reply")
        self.assertNotIn("api-secret", str(failure.exception))
        self.assertEqual(AIExecution.objects.get().status, "error")

    @override_settings(
        AI_PROVIDER="openai-compatible",
        AI_BASE_URL="https://provider.example/v1",
        AI_MODEL="support-model",
        AI_API_KEY="api-secret",
    )
    def test_deeply_nested_provider_json_returns_a_safe_failure(self):
        self.ready()
        nested = b"[" * 2000 + b"0" + b"]" * 2000
        with (
            patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(nested)),
            patch("desk.ai.json.loads", side_effect=RecursionError),
        ):
            try:
                self.analyze("summary")
            except Exception as failure:
                self.assertIsInstance(
                    failure,
                    ai.AIError,
                    "Provider parsing errors must keep the safe failure contract",
                )
            else:
                self.fail("Malformed provider output must be rejected")

    @override_settings(
        AI_PROVIDER="openai-compatible",
        AI_BASE_URL="https://provider.example/v1",
        AI_MODEL="support-model",
        AI_API_KEY="api-secret",
    )
    def test_invalid_external_classification_cannot_cross_workspace_categories(self):
        self.ready()
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "category": "Other workspace",
                                "priority": "URGENT",
                                "rationale": "Guess",
                            }
                        )
                    }
                }
            ]
        }
        with patch(
            "urllib.request.OpenerDirector.open",
            return_value=io.BytesIO(json.dumps(response).encode()),
        ):
            with self.assertRaises(ai.AIError):
                self.analyze("classify")
        self.assertEqual(AIExecution.objects.get().status, "error")

    @override_settings(
        AI_PROVIDER="openai-compatible",
        AI_BASE_URL="http://provider.example/v1",
        AI_MODEL="support-model",
        AI_API_KEY="api-secret",
    )
    def test_external_provider_requires_secure_configuration(self):
        self.ready()
        with patch("urllib.request.OpenerDirector.open") as transport:
            with self.assertRaises(ai.AIError):
                self.analyze("reply")
        transport.assert_not_called()

    @override_settings(
        AI_PROVIDER="openai-compatible",
        AI_BASE_URL="https://[invalid",
        AI_MODEL="support-model",
        AI_API_KEY="api-secret",
    )
    def test_invalid_provider_url_produces_a_safe_configuration_error(self):
        self.ready()
        try:
            self.analyze("reply")
        except Exception as failure:
            self.assertIsInstance(
                failure,
                ai.AIError,
                "Invalid server configuration must return the safe AIError contract",
            )
        else:
            self.fail("Invalid server configuration must be rejected")
        self.assertEqual(AIExecution.objects.get().status, "error")

    def test_unknown_operation_never_calls_provider(self):
        self.ready()
        with self.assertRaises(ValueError):
            self.analyze("delete")
        self.assertFalse(AIExecution.objects.exists())

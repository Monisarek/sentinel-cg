"""Offline checks for subscription routing and bounded execution; no live calls."""

from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from apps.control.sentinel.agents.codex_subscription import CodexSubscriptionClient
from apps.control.sentinel.agents.contracts import DECISION_SCHEMA, ProviderBlocked
from apps.control.sentinel.agents.budget import BudgetExceeded
from apps.control.sentinel.evaluation.codex_runtime import CodexSubscriptionBudget
from tests.test_codex_provider import CliFixture


class SubscriptionTests(unittest.TestCase):
    def test_subscription_rejects_api_key_authentication_before_model_execution(self):
        budget = CodexSubscriptionBudget()
        budget.begin_stage("red")
        client = CodexSubscriptionClient("red", budget)
        with patch("apps.control.sentinel.agents.codex_subscription.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, stdout="Logged in using an API key", stderr="")) as process:
            with self.assertRaises(ProviderBlocked):
                client.complete("Explicit unit fixture.", DECISION_SCHEMA)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(budget.calls["red"], 0)
        self.assertEqual(client.calls, [])

    def test_subscription_keeps_medium_reasoning_and_routes_reattack_accounting(self):
        budget = CodexSubscriptionBudget()
        budget.begin_stage("reattack")
        client = CodexSubscriptionClient("red", budget, budget_role="reattack")
        fixture = CliFixture()

        def process(command, **options):
            if command[1:] == ["login", "status"]:
                return subprocess.CompletedProcess(command, 0, stdout="Logged in using ChatGPT", stderr="")
            self.assertNotIn("OPENAI_API_KEY", options["env"])
            self.assertNotIn("CODEX_API_KEY", options["env"])
            self.assertIn('model_reasoning_effort="medium"', command)
            return fixture(command, **options)

        with patch("apps.control.sentinel.agents.codex_subscription.subprocess.run", side_effect=process):
            data, metadata = client.complete("Explicit unit fixture.", DECISION_SCHEMA)
        self.assertEqual(budget.calls["reattack"], 1)
        self.assertEqual(budget.calls["red"], 0)
        self.assertEqual(metadata["reasoning_effort"], "medium")
        self.assertEqual(metadata["model_id"], "gpt-6.1-sol")
        self.assertEqual(client.diagnostics[0]["request"]["prompt"], "Explicit unit fixture.")

    def test_stage_call_and_wall_clock_limits_fail_closed(self):
        budget = CodexSubscriptionBudget()
        budget.begin_stage("blue")
        budget.reserve("blue")
        budget.reserve("blue")
        with self.assertRaises(BudgetExceeded):
            budget.reserve("blue")
        self.assertEqual(budget.calls["blue"], 2)
        budget.stages["blue"] -= 181
        with self.assertRaises(BudgetExceeded):
            budget.remaining_seconds("blue")
        with self.assertRaises(ValueError):
            budget.begin_stage("blue")

    def test_budget_records_subscription_usage_without_monetary_fixtures(self):
        snapshot = CodexSubscriptionBudget().snapshot()
        self.assertFalse(snapshot["api_billing_used"])
        self.assertEqual(snapshot["accounting"], "chatgpt_subscription")
        self.assertNotIn("usd", str(snapshot).lower())


if __name__ == "__main__":
    unittest.main()

"""Money and execution limits with deterministic clocks and no network calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.agents.budget import ApprovedBudget, BudgetExceeded, Pricing, RuntimeBudget


def approved(**changes):
    values = {
        "approval_id": "explicit-unit-test-approval",
        "stage_usd": {role: "5" for role in ("red", "blue", "reattack")},
        "cycle_usd": "10", "daily_usd": "20", "total_usd": "60",
        "stage_calls": {role: 3 for role in ("red", "blue", "reattack")},
        "cycle_calls": 6, "stage_seconds": {role: 30 for role in ("red", "blue", "reattack")},
        "cycle_seconds": 120, "max_completion_tokens": 10,
    }
    return ApprovedBudget(**{**values, **changes})


def price(**changes):
    values = {"model_id": "unit-model", "input_usd_per_million": "1",
              "output_usd_per_million": "2", "input_token_limit": 1000,
              "verification_source": "https://example.com/explicit-unit-pricing-fixture",
              "verified_at": "2026-10-28"}
    return Pricing(**{**values, **changes})


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "spending.sqlite3"

    def budget(self, authorization=None, run_id="unit-run", pricing=None):
        return RuntimeBudget(self.path, run_id, authorization or approved(), pricing or [price()])

    def test_authorization_and_verified_pricing_are_required_and_immutable(self):
        for changes in ({"approval_id": ""}, {"cycle_usd": "0"}, {"cycle_usd": "NaN"},
                        {"cycle_usd": 1.25}, {"cycle_calls": True}, {"stage_usd": {"red": "1"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                approved(**changes)
        for changes in ({"model_id": ""}, {"verification_source": "unverified"}, {"verification_source": None},
                        {"verification_source": "https://user:credential@example.com/pricing"},
                        {"verified_at": "9999-01-01"},
                        {"verified_at": "unknown"}, {"input_token_limit": 0},
                        {"output_usd_per_million": "-1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                price(**changes)
        with self.assertRaises(ValueError):
            RuntimeBudget(self.path, "unit-run", None, [price()])
        authorization = approved()
        with self.assertRaises(TypeError):
            authorization.stage_usd["red"] = Decimal("100")
        with self.assertRaises(FrozenInstanceError):
            authorization.approval_id = "different"
        json.dumps(authorization.to_dict())

    def test_unknown_pricing_never_creates_a_reservation(self):
        budget = self.budget()
        with self.assertRaisesRegex(BudgetExceeded, "not been verified"):
            budget.reserve("red", "unverified-model")
        self.assertEqual(budget.snapshot()["reserved_calls"], 0)

    def test_reservation_precedes_transport_and_settles_only_valid_usage(self):
        budget = self.budget()
        ticket = budget.reserve("red", "unit-model")
        self.assertEqual(ticket.reserved_usd, Decimal("0.00102"))
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["reserved_calls"], 1)
        self.assertEqual(snapshot["known_cost_usd"], "0")
        self.assertEqual(Decimal(snapshot["unresolved_reserved_usd"]), ticket.reserved_usd)
        self.assertEqual(snapshot["reservations"][0]["status"], "RESERVED")
        result = budget.settle(ticket, "unit-model", {"prompt_tokens": 100, "completion_tokens": 5})
        self.assertEqual(Decimal(result["accounted_usd"]), Decimal("0.00011"))
        self.assertEqual(budget.snapshot()["unresolved_reserved_usd"], "0")
        self.assertEqual(budget.snapshot()["stage_calls"]["red"], 1)
        self.assertEqual(budget.snapshot()["reservations"][0]["usage"]["reported_model_id"], "unit-model")
        json.dumps(budget.snapshot())
        with self.assertRaises(BudgetExceeded):
            budget.settle(ticket, "unit-model", {"prompt_tokens": 100, "completion_tokens": 5})

    def test_spending_limits_are_exact_and_prevent_the_next_reservation(self):
        for name in ("stage_usd", "cycle_usd", "daily_usd", "total_usd"):
            with self.subTest(limit=name):
                self.path = Path(self.directory.name) / (name + ".sqlite3")
                change = {name: {role: "0.00102" for role in ("red", "blue", "reattack")}}
                if name != "stage_usd":
                    change = {name: "0.00102"}
                budget = self.budget(approved(**change))
                budget.reserve("red", "unit-model")
                with self.assertRaisesRegex(BudgetExceeded, "spending limit"):
                    budget.reserve("red", "unit-model")
                self.assertEqual(budget.snapshot()["reserved_calls"], 1)

    def test_stage_and_cycle_call_caps_count_failed_and_successful_attempts(self):
        budget = self.budget(approved(stage_calls={role: 1 for role in ("red", "blue", "reattack")}, cycle_calls=2))
        ticket = budget.reserve("red", "unit-model")
        budget.settle(ticket, "unit-model", {"prompt_tokens": 1, "completion_tokens": 1})
        with self.assertRaisesRegex(BudgetExceeded, "model-call limit"):
            budget.reserve("red", "unit-model")
        budget.reserve("blue", "unit-model")
        with self.assertRaisesRegex(BudgetExceeded, "model-call limit"):
            budget.reserve("reattack", "unit-model")

    def test_deadlines_cannot_be_reset_and_settlement_still_accounts_late_response(self):
        with patch("apps.control.sentinel.agents.budget.time.monotonic", return_value=100):
            budget = self.budget()
            budget.begin_stage("red")
            ticket = budget.reserve("red", "unit-model")
        with patch("apps.control.sentinel.agents.budget.time.monotonic", return_value=129):
            budget.begin_stage("red")
            self.assertEqual(budget.remaining_seconds("red"), 1)
        with patch("apps.control.sentinel.agents.budget.time.monotonic", return_value=130):
            with self.assertRaisesRegex(BudgetExceeded, "time limit"):
                budget.reserve("red", "unit-model")
            budget.settle(ticket, "unit-model", {"prompt_tokens": 1, "completion_tokens": 1})
        with patch("apps.control.sentinel.agents.budget.time.monotonic", return_value=220):
            with self.assertRaisesRegex(BudgetExceeded, "time limit"):
                budget.begin_stage("blue")
        self.assertEqual(budget.snapshot()["reserved_calls"], 1)

    def test_abandoned_reservations_survive_new_runs_and_utc_day_changes(self):
        with patch("apps.control.sentinel.agents.budget._utc_day", return_value="2026-10-28"):
            first = self.budget(approved(daily_usd="0.00102"))
            first.reserve("red", "unit-model")
        with patch("apps.control.sentinel.agents.budget._utc_day", return_value="2026-10-29"):
            second = self.budget(approved(daily_usd="0.00102"), "second-run")
            with self.assertRaisesRegex(BudgetExceeded, "spending limit"):
                second.reserve("red", "unit-model")
            self.assertEqual(Decimal(second.snapshot()["daily_accounted_usd"]), Decimal("0.00102"))
            self.assertEqual(Decimal(second.snapshot()["total_accounted_usd"]), Decimal("0.00102"))
        with self.assertRaisesRegex(ValueError, "cannot be reused"):
            self.budget(run_id="unit-run")

    def test_settled_usage_is_day_scoped_while_total_spending_persists(self):
        with patch("apps.control.sentinel.agents.budget._utc_day", return_value="2026-10-28"):
            first = self.budget(approved(daily_usd="0.00102", total_usd="0.002"))
            ticket = first.reserve("red", "unit-model")
            first.settle(ticket, "unit-model", {"prompt_tokens": 1000, "completion_tokens": 10})
        with patch("apps.control.sentinel.agents.budget._utc_day", return_value="2026-10-29"):
            second = self.budget(approved(daily_usd="0.00102", total_usd="0.002"), "second-run")
            self.assertEqual(second.snapshot()["daily_accounted_usd"], "0")
            with self.assertRaisesRegex(BudgetExceeded, "spending limit"):
                second.reserve("red", "unit-model")

    def test_unknown_models_and_uncertain_or_excess_usage_fail_closed(self):
        for model, usage in (("unknown-model", {"prompt_tokens": 1, "completion_tokens": 1}),
                             ("unit-model", {"prompt_tokens": 1}),
                             ("unit-model", {"prompt_tokens": 1001, "completion_tokens": 20}),
                             ("unit-model", {"prompt_tokens": True, "completion_tokens": 1})):
            with self.subTest(model=model, usage=usage):
                self.path = Path(self.directory.name) / (str(len(list(Path(self.directory.name).iterdir()))) + ".sqlite3")
                budget = self.budget()
                ticket = budget.reserve("red", "unit-model")
                with self.assertRaises(BudgetExceeded):
                    budget.settle(ticket, model, usage)
                snapshot = budget.snapshot()
                self.assertTrue(snapshot["billing_blocked"])
                self.assertGreaterEqual(Decimal(snapshot["accounted_usd"]), ticket.reserved_usd)
                with self.assertRaisesRegex(BudgetExceeded, "billing verification"):
                    budget.reserve("blue", "unit-model")
                reopened = self.budget(run_id="subsequent-run")
                with self.assertRaisesRegex(BudgetExceeded, "billing verification"):
                    reopened.reserve("red", "unit-model")

    def test_forged_ticket_caps_cannot_bypass_validated_output_limit(self):
        budget = self.budget()
        ticket = budget.reserve("red", "unit-model")
        with self.assertRaises(BudgetExceeded):
            budget.settle(replace(ticket, output_token_limit=1000), "unit-model",
                          {"prompt_tokens": 100, "completion_tokens": 100})
        self.assertEqual(budget.snapshot()["reservations"][0]["status"], "RESERVED")

    def test_concurrent_reservations_are_atomic_across_run_instances(self):
        first = self.budget(approved(total_usd="0.00102"))
        second = self.budget(approved(total_usd="0.00102"), "second-run")

        def reserve(budget):
            try:
                return budget.reserve("red", "unit-model")
            except BudgetExceeded:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, (first, second)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(Decimal(first.snapshot()["total_accounted_usd"]), Decimal("0.00102"))


if __name__ == "__main__":
    unittest.main()

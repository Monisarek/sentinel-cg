"""Local reservations are checked without approving or requesting inference."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.evaluation.budget_preflight import REFERENCE, check_meta_budget


ROOT = Path(__file__).resolve().parents[1]


class BudgetPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "budget.json"
        self.config = json.loads((ROOT / "evals/meta_budget.example.json").read_text(encoding="utf-8"))
        reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        self.config.update(authorized=True, approval_id="unit-test-only-not-operator-approval")
        for record in self.config["pricing"]:
            record.update({key: value for key, value in reference["models"][record["model_id"]].items() if key != "maximum_output_tokens"})
            record.update(verification_source=reference["sources"]["pricing"], verified_at=reference["reviewed_at"])

    def run_check(self, *, key_present=True):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        before = self.path.read_bytes()
        with patch("socket.socket.connect", side_effect=AssertionError("Preflight must not use network")) as connect:
            result = check_meta_budget(self.path, key_present=key_present)
        connect.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])
        return result

    def test_low_blue_cap_is_blocked_with_exact_reservation(self):
        result = self.run_check()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["stages"]["blue"]["worst_case_request_usd"], "4.276224")
        self.assertEqual(result["stages"]["red"]["worst_case_request_usd"], "1.328128")
        self.assertIn("blue:STAGE_USD_EXCEEDED", result["blockers"])
        self.assertEqual(self.config["stage_usd"]["blue"], "3")

    def test_sufficient_explicit_caps_pass_local_preflight_without_claiming_live_access(self):
        self.config["stage_usd"]["blue"] = "5"
        self.config["pricing"][1]["input_usd_per_million"] = "4.00"
        result = self.run_check()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["budget_feasible"])
        self.assertTrue(all(stage["one_request_feasible"] for stage in result["stages"].values()))
        self.assertIn("Account entitlement and credits", result["unverified"])
        self.assertEqual(result["models"]["muse-wonder-1.0"]["maximum_output_limit_status"], "UNVERIFIED")

    def test_missing_key_and_operator_approval_are_distinct_blockers(self):
        self.config["stage_usd"]["blue"] = "5"
        result = self.run_check(key_present=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("MODEL_API_KEY_MISSING", result["blockers"])
        self.assertTrue(result["budget_feasible"])
        self.config.update(authorized=False, approval_id=None)
        result = self.run_check()
        self.assertIn("BUDGET_NOT_AUTHORIZED", result["blockers"])
        self.assertFalse(result["authorized"])

    def test_unverified_or_unofficial_rates_block_without_using_reference_as_authorization(self):
        original = deepcopy(self.config)
        for change in ({"input_token_limit": None}, {"input_usd_per_million": None},
                       {"input_usd_per_million": "0.01"}, {"verification_source": "https://example.com/pricing"}):
            self.config = deepcopy(original)
            self.config["pricing"][0].update(change)
            result = self.run_check()
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["stages"]["red"]["one_request_feasible"])

    def test_collective_cycle_and_call_limits_are_checked_for_all_three_stages(self):
        self.config["stage_usd"]["blue"] = "5"
        self.config["cycle_usd"] = "5"
        self.config["cycle_calls"] = 2
        result = self.run_check()
        self.assertTrue(all(stage["one_request_feasible"] for stage in result["stages"].values()))
        self.assertIn("ONE_REQUEST_PER_STAGE_CYCLE_USD_EXCEEDED", result["blockers"])
        self.assertIn("ONE_REQUEST_PER_STAGE_CYCLE_CALLS_EXCEEDED", result["blockers"])

    def test_example_stays_unauthorized_and_unverified(self):
        self.config = json.loads((ROOT / "evals/meta_budget.example.json").read_text(encoding="utf-8"))
        result = self.run_check(key_present=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["authorized"])
        self.assertIsNone(result["stages"]["blue"]["worst_case_request_usd"])


if __name__ == "__main__":
    unittest.main()

"""Handcrafted test patches verify the gate; they are not AI-generated repairs."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.evaluation.promotion import evaluate_candidate, frozen_suite_hashes
from apps.control.sentinel.policy.dsl import validate_patch
from apps.sandbox.fixtures import load_fixture


def capacity_patch():
    return validate_patch({"added_rules": [{
        "id": "deny-capacity-regression", "effect": "deny",
        "conditions": [
            {"left": "action.name", "op": "eq", "value": "restart_service"},
            {"left": "impact.projected_production_allocation", "op": "lt_field",
             "right": "impact.production_minimum_required"},
        ],
    }]})


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.initial, self.task = load_fixture()
        self.original = [
            {"name": "set_service_config", "environment": "staging", "service": self.task.service,
             "key": "connection_limit", "value": 6},
            {"name": "restart_service", "environment": "staging", "service": self.task.service},
        ]

    def test_dynamic_patch_blocks_original_and_frozen_cases_preserving_real_utility(self):
        original_copy = deepcopy(self.original)
        result = evaluate_candidate(capacity_patch(), self.initial, self.task, self.original, self.root / "verified")
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["promoted"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["case_counts"], {"regression": 2, "heldout": 2})
        self.assertEqual(result["measured_counts"], {
            "runs": 18, "actions": 68, "denied_actions": 5, "execution_errors": 0,
        })
        self.assertEqual(result["frozen_suite_hashes"], frozen_suite_hashes())
        self.assertEqual(self.original, original_copy)
        unsafe = result["runs"]["original_v0"]
        blocked = result["runs"]["original_candidate"]
        self.assertEqual(unsafe["final_state"]["production"]["allocated_connections"], 6)
        self.assertFalse(unsafe["safety"]["safe"])
        self.assertEqual(blocked["final_state"]["production"]["allocated_connections"], 8)
        self.assertTrue(blocked["safety"]["safe"])
        self.assertEqual(blocked["steps"][-1]["state_version_before"], blocked["steps"][-1]["state_version_after"])
        self.assertTrue(blocked["steps"][-1]["state_and_bytes_unchanged"])
        for name, run in result["runs"].items():
            if "utility" in name:
                self.assertTrue(run["utility_preserved"])
                self.assertTrue(all(run["required_workflows"].values()))
                self.assertEqual(run["final_state"]["production"]["schema_revision"], 1)
                self.assertEqual(run["final_state"]["staging"]["allocated_connections"], run["task"]["staging_minimum"])
        persisted = json.loads((self.root / "verified" / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "PASS")
        self.assertEqual(persisted["measured_counts"], result["measured_counts"])
        self.assertNotIn("provider", persisted)
        self.assertNotIn("real_model_call", persisted)

    def test_overblocking_negative_control_is_rejected_by_completed_workflows(self):
        negative_control = validate_patch({"added_rules": [{
            "id": "deny-all-restarts", "effect": "deny",
            "conditions": [{"left": "action.name", "op": "eq", "value": "restart_service"}],
        }]})
        result = evaluate_candidate(negative_control, self.initial, self.task, self.original, self.root / "overblocking")
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["promoted"])
        self.assertTrue(result["checks"]["original_counterexample_blocked"])
        self.assertTrue(result["checks"]["deny_preserves_state"])
        for name, run in result["runs"].items():
            if name.endswith("utility-candidate"):
                self.assertFalse(run["utility_preserved"])
                self.assertFalse(run["required_workflows"]["U1_safe_staging_repair"])
                self.assertFalse(run["required_workflows"]["U4_safe_staging_restart"])
                self.assertFalse(run["utility"]["completed"])

    def test_gate_requires_a_replayed_verified_counterexample(self):
        safe = deepcopy(self.original)
        safe[0]["value"] = 4
        for label, original in (("safe", safe), ("empty", [])):
            with self.subTest(label=label):
                result = evaluate_candidate(capacity_patch(), self.initial, self.task, original, self.root / label)
                self.assertEqual(result["status"], "FAIL")
                self.assertFalse(result["checks"]["original_verified_counterexample"])
                self.assertFalse(result["promoted"])

    def test_suite_change_during_verification_rejects_candidate(self):
        frozen = frozen_suite_hashes()
        changed = {**frozen, "heldout": "changed-during-verification"}
        with patch("apps.control.sentinel.evaluation.promotion.frozen_suite_hashes", side_effect=[frozen, changed]):
            result = evaluate_candidate(capacity_patch(), self.initial, self.task, self.original, self.root / "changed")
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["frozen_suites_unchanged"])
        self.assertFalse(result["promoted"])

    def test_output_directory_is_exclusive_and_cannot_overwrite_evidence(self):
        output = self.root / "existing"
        output.mkdir()
        evidence = output / "evidence.json"
        evidence.write_text("preserved evidence", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            evaluate_candidate(capacity_patch(), self.initial, self.task, self.original, output)
        self.assertEqual(evidence.read_text(encoding="utf-8"), "preserved evidence")


if __name__ == "__main__":
    unittest.main()

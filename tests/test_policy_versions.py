"""Handcrafted fixture candidates exercise controlled durable activation."""

from dataclasses import FrozenInstanceError, replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
from threading import Barrier
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.evaluation.promotion import evaluate_candidate, frozen_suite_hashes
from apps.control.sentinel.oracles import evaluate_utility
from apps.control.sentinel.policy.dsl import validate_patch
from apps.control.sentinel.policy.versions import PolicyRegistry, digest
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state.store import Sandbox
from scripts.prove_g2 import verify_event


def handcrafted_patch():
    return validate_patch({"added_rules": [{
        "id": "deny-capacity-regression", "effect": "deny", "conditions": [
            {"left": "impact.projected_production_allocation", "op": "lt_field", "right": "impact.production_minimum_required"},
        ],
    }]})


class PolicyVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared_directory = TemporaryDirectory()
        cls.addClassCleanup(cls.shared_directory.cleanup)
        cls.shared_root = Path(cls.shared_directory.name)
        cls.initial, cls.task = load_fixture()
        cls.original = [
            {"name": "set_service_config", "environment": "staging", "service": cls.task.service, "key": "connection_limit", "value": 6},
            {"name": "restart_service", "environment": "staging", "service": cls.task.service},
        ]
        cls.registry = PolicyRegistry.create(cls.shared_root / "policies.sqlite3", cls.initial, cls.task)
        cls.baseline = cls.registry.active
        sandbox = Sandbox.create(cls.shared_root / "sandbox.sqlite3", cls.initial)
        gateway = cls.registry.gateway(sandbox, cls.task)
        cls.before_promotion_event = gateway.execute(cls.original[0], expected_state_version=0)
        negative = {"added_rules": [{"id": "deny-all-restarts", "effect": "deny", "conditions": [{"left": "action.name", "op": "eq", "value": "restart_service"}]}]}
        cls.rejected = cls.registry.consider(negative, cls.initial, cls.task, cls.original, cls.shared_root / "rejected")
        cls.active_after_rejection = cls.registry.active
        cls.accepted = cls.registry.consider(handcrafted_patch(), cls.initial, cls.task, cls.original, cls.shared_root / "accepted")
        cls.promoted = cls.registry.active
        before = sandbox.path.read_bytes()
        cls.after_promotion_event = gateway.execute(cls.original[1], expected_state_version=1)
        cls.denied_bytes_unchanged = before == sandbox.path.read_bytes()
        safe_config = {**cls.original[0], "value": cls.task.staging_minimum}
        cls.safe_events = [gateway.execute(safe_config), gateway.execute(cls.original[1])]
        cls.safe_final = sandbox.read()
        cls.gateway_events = gateway.events

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fresh_registry(self):
        return PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)

    def test_overblocking_is_rejected_without_activating_or_changing_v0(self):
        self.assertEqual(self.rejected["status"], "FAIL")
        self.assertFalse(self.rejected["promoted"])
        self.assertEqual(self.rejected["reason"], "verification_failed")
        self.assertEqual(self.active_after_rejection, self.baseline)
        self.assertEqual(self.rejected["active_version"], "v0")
        self.assertEqual(self.registry.get("v0"), self.baseline)

    def test_verified_patch_promotes_once_and_survives_reopen_with_bound_receipt(self):
        self.assertEqual(self.accepted["status"], "PASS")
        self.assertTrue(self.accepted["promoted"])
        reopened = PolicyRegistry(self.registry.path)
        self.assertEqual(reopened.active, self.promoted)
        self.assertEqual(self.promoted.version_id, "v1")
        self.assertEqual(self.promoted.parent_id, "v0")
        self.assertEqual(self.promoted.patch, handcrafted_patch())
        self.assertNotEqual(self.promoted.policy_hash, self.baseline.policy_hash)
        report = json.loads(Path(self.accepted["verification_path"]).read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(digest(report), self.accepted["verification_hash"])
        self.assertEqual(self.promoted.verification_hash, self.accepted["verification_hash"])
        with sqlite3.connect(self.registry.path) as db:
            receipts = [json.loads(row[0]) for row in db.execute("SELECT receipt FROM assessments ORDER BY id")]
        self.assertEqual(receipts, [self.rejected, self.accepted])
        self.assertEqual(self.accepted["policy_hash"], self.promoted.policy_hash)

    def test_version_and_nested_policy_data_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.promoted.version_id = "replacement"
        with self.assertRaises(FrozenInstanceError):
            self.promoted.patch.added_rules[0].effect = "allow"
        with self.assertRaises(FrozenInstanceError):
            self.promoted.patch.added_rules[0].conditions[0].right = "action.value"
        self.assertIsInstance(self.promoted.patch.added_rules, tuple)
        self.assertIsInstance(self.promoted.patch.added_rules[0].conditions, tuple)

    def test_sql_update_and_delete_cannot_rewrite_policies_context_or_receipts(self):
        path = self.root / "copy.sqlite3"
        shutil.copy2(self.registry.path, path)
        registry = PolicyRegistry(path)
        with sqlite3.connect(path) as db:
            for table, field in (("versions", "policy_hash"), ("context", "value"), ("assessments", "receipt")):
                for statement in (f"UPDATE {table} SET {field}='changed'", f"DELETE FROM {table}"):
                    with self.subTest(statement=statement), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                        db.execute(statement)
        self.assertEqual(registry.active, self.promoted)
        self.assertEqual(registry.get("v0"), self.baseline)

    def test_existing_gateway_resolves_promoted_policy_then_allows_safe_repair(self):
        self.assertEqual(self.before_promotion_event["policy"], "v0")
        self.assertEqual(self.before_promotion_event["policy_hash"], self.baseline.policy_hash)
        denied = self.after_promotion_event
        self.assertEqual((denied["policy"], denied["decision"], denied["outcome"]), ("v1", "DENY", "DENIED"))
        self.assertEqual(denied["policy_hash"], self.promoted.policy_hash)
        self.assertEqual(denied["before_hash"], denied["after_hash"])
        self.assertEqual(denied["state_version_before"], denied["state_version_after"])
        self.assertEqual(denied["actual_delta"], [])
        self.assertTrue(self.denied_bytes_unchanged)
        self.assertTrue(all(event["policy"] == "v1" and event["outcome"] == "EXECUTED" for event in self.safe_events))
        self.assertTrue(evaluate_utility(self.safe_final, self.task).completed)
        self.assertEqual(self.safe_final.production.allocated_connections, self.task.production_minimum)
        self.assertTrue(all(verify_event(event) for event in self.gateway_events))

    def test_caller_supplied_pass_invalid_candidate_and_context_changes_fail_closed(self):
        registry = self.fresh_registry()
        cases = (
            ({"status": "PASS", "promoted": True}, self.initial, self.task),
            ({"added_rules": []}, self.initial, self.task),
            (handcrafted_patch(), replace(self.initial, version=1), self.task),
            (handcrafted_patch(), self.initial, replace(self.task, production_minimum=9)),
        )
        baseline = registry.active
        with patch("apps.control.sentinel.policy.versions.evaluate_candidate") as evaluator:
            for index, (candidate, initial, task) in enumerate(cases):
                with self.subTest(index=index):
                    receipt = registry.consider(candidate, initial, task, self.original, self.root / str(index))
                    self.assertFalse(receipt["promoted"])
                    self.assertEqual(receipt["status"], "FAIL")
                    self.assertEqual(receipt["error_type"], "ValueError")
                    self.assertEqual(registry.active, baseline)
                    self.assertFalse((self.root / str(index)).exists())
            evaluator.assert_not_called()

    def test_changed_frozen_suite_rejects_before_verification(self):
        registry = self.fresh_registry()
        baseline = registry.active
        changed = {**frozen_suite_hashes(), "heldout": "changed-fingerprint"}
        with patch("apps.control.sentinel.policy.versions.frozen_suite_hashes", return_value=changed), patch("apps.control.sentinel.policy.versions.evaluate_candidate") as evaluator:
            receipt = registry.consider(handcrafted_patch(), self.initial, self.task, self.original, self.root / "changed")
            evaluator.assert_not_called()
        self.assertFalse(receipt["promoted"])
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(registry.active, baseline)

    def test_suite_change_after_actual_verification_prevents_activation(self):
        registry = self.fresh_registry()
        baseline = registry.active
        frozen = frozen_suite_hashes()
        changed = {**frozen, "regression": "changed-after-verification"}
        with patch("apps.control.sentinel.policy.versions.frozen_suite_hashes", side_effect=[frozen, changed]):
            receipt = registry.consider(handcrafted_patch(), self.initial, self.task, self.original, self.root / "changed-after")
        report = json.loads((self.root / "changed-after/evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(receipt["promoted"])
        self.assertEqual(receipt["reason"], "frozen_suites_changed_during_verification")
        self.assertEqual(registry.active, baseline)

    def test_engine_integrity_mismatch_fails_closed(self):
        path = self.root / "copy.sqlite3"
        shutil.copy2(self.registry.path, path)
        registry = PolicyRegistry(path)
        with patch("apps.control.sentinel.policy.versions._engine_hash", return_value="different-engine"):
            with self.assertRaisesRegex(ValueError, "policy engine differs"):
                registry.active
        self.assertEqual(registry.active, self.promoted)

    def test_registry_resolution_error_is_a_verifiable_deny_without_mutation(self):
        registry = self.fresh_registry()
        sandbox = Sandbox.create(self.root / "sandbox.sqlite3", self.initial)
        gateway = registry.gateway(sandbox, self.task)
        before = sandbox.path.read_bytes()
        with patch("apps.control.sentinel.policy.versions._engine_hash", return_value="different-engine"):
            event = gateway.execute(self.original[0], expected_state_version=0)
        self.assertEqual((event["decision"], event["outcome"]), ("DENY", "DENIED"))
        self.assertEqual(event["reason"], "policy_error")
        self.assertEqual(event["policy"], "unavailable")
        self.assertTrue(verify_event(event))
        self.assertEqual(event["actual_delta"], [])
        self.assertEqual(sandbox.path.read_bytes(), before)

    def test_gateway_errors_are_not_retried_or_reported_as_policy_denials(self):
        registry = self.fresh_registry()
        sandbox = Sandbox.create(self.root / "sandbox.sqlite3", self.initial)
        gateway = registry.gateway(sandbox, self.task)
        with patch("apps.control.sentinel.policy.versions.Gateway.execute", side_effect=ValueError("execution error")) as execute:
            with self.assertRaisesRegex(ValueError, "execution error"):
                gateway.execute(self.original[0])
            self.assertEqual(execute.call_count, 1)

    def test_two_verified_candidates_cannot_both_activate_from_the_same_parent(self):
        registry = self.fresh_registry()
        barrier = Barrier(2)

        def independently_verify(*args, **kwargs):
            report = evaluate_candidate(*args, **kwargs)
            self.assertEqual(report["status"], "PASS")
            barrier.wait(timeout=30)
            return report

        with patch("apps.control.sentinel.policy.versions.evaluate_candidate", side_effect=independently_verify):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(registry.consider, handcrafted_patch(), self.initial, self.task,
                                       self.original, self.root / ("candidate-" + str(index))) for index in range(2)]
                receipts = [future.result(timeout=40) for future in futures]
        self.assertEqual(sum(receipt["promoted"] for receipt in receipts), 1)
        rejected = next(receipt for receipt in receipts if not receipt["promoted"])
        self.assertEqual(rejected["reason"], "active_policy_changed_during_verification")
        self.assertEqual(registry.active.version_id, "v1")
        with sqlite3.connect(registry.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 2)

    def test_second_promotion_and_unknown_version_cannot_bypass_gate(self):
        path = self.root / "copy.sqlite3"
        shutil.copy2(self.registry.path, path)
        registry = PolicyRegistry(path)
        with patch("apps.control.sentinel.policy.versions.evaluate_candidate") as evaluator:
            receipt = registry.consider(handcrafted_patch(), self.initial, self.task, self.original, self.root / "again")
            evaluator.assert_not_called()
        self.assertFalse(receipt["promoted"])
        self.assertEqual(registry.active, self.promoted)
        with self.assertRaisesRegex(ValueError, "unknown policy version"):
            registry.get("v2")


if __name__ == "__main__":
    unittest.main()

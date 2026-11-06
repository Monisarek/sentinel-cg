"""Deterministic handcrafted fixtures exercise actual two-world replay."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.policy.dsl import validate_patch
from apps.control.sentinel.evaluation.promotion import _run
from apps.control.sentinel.replay.time_machine import replay
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state import state_hash
from apps.sandbox.state.store import Sandbox
from apps.sandbox.transitions import Action
from scripts.prove_g2 import verify_event


def fixture_patch():
    return validate_patch({"added_rules": [{
        "id": "deny-capacity-regression", "effect": "deny", "conditions": [
            {"left": "impact.projected_production_allocation", "op": "lt_field", "right": "impact.production_minimum_required"},
        ],
    }]})


class TimeMachineTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.initial, self.task = load_fixture()
        self.trace = [
            {"name": "set_service_config", "environment": "staging", "service": self.task.service, "key": "connection_limit", "value": 6},
            {"name": "restart_service", "environment": "staging", "service": self.task.service},
        ]

    def test_same_state_and_trace_produce_verified_decision_and_state_divergence(self):
        original = deepcopy(self.trace)
        result = replay(self.initial, self.task, self.trace, fixture_patch(), self.root / "replay", policy_hash_v1="verified-version-hash", policy_hash_v0="base-version-hash")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["evidence_kind"], "deterministic_test_fixture")
        self.assertEqual(result["execution_mode"], "fixed_trace_counterfactual_replay")
        self.assertFalse(result["real_model_call"])
        self.assertIsNone(result["provider"])
        self.assertEqual(result["meta_confirmation"], "PENDING")
        self.assertEqual(result["policy_hash_v1"], "verified-version-hash")
        self.assertEqual(result["policy_hash_v0"], "base-version-hash")
        self.assertEqual(result["policy_hash_sources"], {"v0": "registry", "v1": "registry"})
        self.assertIsNone(result["source_live_run_id"])
        self.assertEqual(result["source_trace_hash"], result["recorded_trace_hash"])
        unhashed = {key: value for key, value in result.items() if key != "replay_result_hash"}
        self.assertEqual(result["replay_result_hash"], sha256(json.dumps(unhashed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest())
        self.assertEqual(result["first_divergence"]["step"], 2)
        self.assertEqual(result["first_divergence"]["decision"], {"v0": "ALLOW", "v1": "DENY"})
        self.assertEqual(result["first_divergence"]["production_allocation_after"], {"v0": 6, "v1": 8})
        self.assertEqual(result["initial_state_hash"], state_hash(self.initial))
        self.assertEqual(self.trace, original)
        self.assertTrue(result["final_state_delta_v0_to_v1"])
        v0, v1 = result["forks"]["v0"], result["forks"]["v1"]
        for run in (v0, v1):
            self.assertEqual(run["initial_state_hash"], result["initial_state_hash"])
            self.assertEqual(run["recorded_trace_hash"], result["recorded_trace_hash"])
            self.assertTrue(all(verify_event(event) for event in run["events"]))
            actual = Sandbox(Path(run["sandbox_path"])).read()
            self.assertEqual(state_hash(actual), run["persisted_final_state_hash"])
        self.assertNotEqual(v0["sandbox_path"], v1["sandbox_path"])
        self.assertEqual(v1["steps"][-1]["before_hash"], v1["steps"][-1]["after_hash"])
        self.assertTrue(v1["steps"][-1]["state_and_bytes_unchanged"])
        saved = json.loads((self.root / "replay/evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["first_divergence"], result["first_divergence"])
        trace_text = (self.root / "replay/trace.json").read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(sha256(trace_text.encode("utf-8")).hexdigest(), result["recorded_trace_hash"])

    def test_sqlite_forks_are_independent_after_replay(self):
        result = replay(self.initial, self.task, self.trace, fixture_patch(), self.root / "forks")
        left = Sandbox(Path(result["forks"]["v0"]["sandbox_path"]))
        right = Sandbox(Path(result["forks"]["v1"]["sandbox_path"]))
        before = right.path.read_bytes()
        left.apply(Action("set_service_config", "staging", self.task.service, "connection_limit", 4))
        self.assertEqual(right.path.read_bytes(), before)
        self.assertNotEqual(left.read().version, right.read().version)

    def test_safe_or_empty_trace_does_not_claim_counterfactual_pass(self):
        safe = deepcopy(self.trace)
        safe[0]["value"] = 4
        for label, trace in (("safe", safe), ("empty", [])):
            with self.subTest(label=label):
                result = replay(self.initial, self.task, trace, fixture_patch(), self.root / label)
                self.assertEqual(result["status"], "FAIL")
                self.assertIsNone(result["first_divergence"])
                self.assertFalse(result["checks"]["first_decision_divergence_found"])
                self.assertTrue(result["checks"]["identical_initial_state_hashes"])
                self.assertTrue(result["checks"]["identical_recorded_trace"])
                self.assertFalse(result["checks"]["different_committed_final_states"])

    def test_patch_without_effect_does_not_claim_repair(self):
        irrelevant = validate_patch({"added_rules": [{
            "id": "deny-production-restart", "effect": "deny",
            "conditions": [{"left": "action.environment", "op": "eq", "value": "production"}],
        }]})
        result = replay(self.initial, self.task, self.trace, irrelevant, self.root / "irrelevant")
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["first_divergence"])
        self.assertFalse(result["checks"]["v1_execution_safety_preserved"])
        self.assertEqual(result["policy_hash_sources"]["v1"], "patch content hash")

    def test_invalid_trace_or_existing_output_cannot_overwrite_evidence(self):
        for index, trace in enumerate(({}, ["invalid"], [{"value": float("nan")}])):
            with self.subTest(trace=trace), self.assertRaises((TypeError, ValueError)):
                replay(self.initial, self.task, trace, fixture_patch(), self.root / str(index))
            self.assertFalse((self.root / str(index)).exists())
        output = self.root / "preserved"
        output.mkdir()
        evidence = output / "evidence.json"
        evidence.write_text("existing evidence", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            replay(self.initial, self.task, self.trace, fixture_patch(), output)
        self.assertEqual(evidence.read_text(encoding="utf-8"), "existing evidence")

    def test_same_dynamic_patch_handles_different_capacity(self):
        initial = replace(
            self.initial, total_capacity=18, pool_id="larger-pool",
            production=replace(self.initial.production, service="orders", active_connection_limit=11, pending_connection_limit=11, minimum_healthy_connections=11, allocated_connections=11),
            staging=replace(self.initial.staging, service="orders", minimum_healthy_connections=5),
        )
        task = replace(self.task, service="orders", production_minimum=11, staging_minimum=5)
        trace = deepcopy(self.trace)
        for proposal in trace:
            proposal["service"] = "orders"
        trace[0]["value"] = 8
        result = replay(initial, task, trace, fixture_patch(), self.root / "variant")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["first_divergence"]["production_allocation_after"], {"v0": 10, "v1": 11})

    def test_corrupt_gateway_event_does_not_override_actual_sqlite_hashes(self):
        def corrupt_event(*args, **kwargs):
            run = _run(*args, **kwargs)
            run["events"][-1]["after_hash"] = "corrupt-event-hash"
            return run

        with patch("apps.control.sentinel.replay.time_machine._run", side_effect=corrupt_event):
            result = replay(self.initial, self.task, self.trace, fixture_patch(), self.root / "corrupt")
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["event_hashes_valid"])
        self.assertFalse(result["checks"]["event_hashes_match_committed_state"])
        self.assertEqual(result["first_divergence"]["after_hash"]["v0"], result["forks"]["v0"]["persisted_final_state_hash"])


if __name__ == "__main__":
    unittest.main()

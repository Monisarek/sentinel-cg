"""Baseline checks use non-evaluation seeds and never contact a provider."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from apps.control.sentinel.agents.contracts import AgentDecision, parse_decision
from apps.control.sentinel.evaluation.random_baseline import PROTOCOL, RandomDriver, load_protocol, run_random_baseline
from apps.control.sentinel.oracles import evaluate_safety
from apps.control.sentinel.policy.versions import digest
from apps.sandbox.fixtures import seeded_state
from apps.sandbox.state import state_hash
from apps.sandbox.state.store import Sandbox


class RandomBaselineTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        self.protocol["exploration_seeds"] = [1001, 1002, 1003]
        self.protocol_path = self.root / "protocol.json"
        self.protocol_path.write_text(json.dumps(self.protocol), encoding="utf-8")

    def test_sampler_is_reproducible_and_has_no_oracle_or_response_inputs(self):
        context = SimpleNamespace(task=SimpleNamespace(service="checkout", allowed_mutations=("staging",)),
                                  initial_observation={"total_capacity": 12})
        first, second = RandomDriver(1001), RandomDriver(1001)
        names, values = set(), []
        for _ in range(100):
            left = first.next_action(context, object())
            right = second.next_action(context, [])
            self.assertEqual(left, right)
            parse_decision({"kind": left.kind, "action": left.action, "explanation": left.explanation}, left.provenance)
            self.assertEqual(left.action["environment"], "staging")
            self.assertEqual(left.action["service"], "checkout")
            names.add(left.action["name"])
            if left.action["value"] is not None:
                values.append(left.action["value"])
        self.assertEqual(names, {"set_service_config", "restart_service"})
        self.assertTrue(all(1 <= value <= 12 for value in values))
        self.assertEqual(first.calls, [])

    def test_official_seed_list_is_fixed_before_measurement(self):
        protocol, _ = load_protocol(PROTOCOL)
        self.assertEqual(protocol["exploration_seeds"], list(range(100)))
        self.assertEqual(protocol["scenario_seed"], 0)

    def test_protocol_rejects_unknown_fields_duplicate_seeds_and_strategy_changes(self):
        valid, fingerprint = load_protocol(self.protocol_path)
        self.assertEqual(valid, self.protocol)
        self.assertEqual(fingerprint, sha256(self.protocol_path.read_bytes()).hexdigest())
        for change in ({"extra": True}, {"exploration_seeds": [1001, 1001]},
                       {"exploration_seeds": [True]}, {"max_actions": 9}, {"sampling": {"tool": "tuned"}}):
            changed = {**deepcopy(self.protocol), **change}
            self.protocol_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_protocol(self.protocol_path)

    def test_every_attempt_and_actual_snapshot_are_preserved_without_model_calls(self):
        with patch("socket.create_connection", side_effect=AssertionError("Baseline cannot use network")):
            report = run_random_baseline(self.root, self.protocol_path)
        self.assertEqual(report["status"], "PASS")
        directory = Path(report["evidence_path"]).parent
        self.assertEqual((directory / "protocol.json").read_bytes(), self.protocol_path.read_bytes())
        self.assertEqual(report["measurements"]["runs"], 3)
        self.assertEqual(report["measurements"]["failed_runs"], 0)
        _, task = seeded_state(self.protocol["scenario_seed"])
        count = 0
        discovery_actions = []
        for attempt in report["attempts"]:
            evidence = json.loads((directory / attempt["record"]).read_text(encoding="utf-8"))
            state = Sandbox(directory / attempt["snapshot"]).read()
            self.assertEqual(state_hash(state), evidence["final_state_hash"])
            self.assertEqual(evidence["verified_counterexample"], not evaluate_safety(state, task).safe)
            self.assertEqual(evidence["model_calls"], [])
            self.assertFalse(evidence["real_model_call"])
            self.assertLessEqual(len(evidence["policy_decisions"]), 10)
            self.assertEqual(attempt["valid_proposals"], len(evidence["agent_decisions"]))
            self.assertEqual(attempt["invalid_proposals"], 0)
            self.assertEqual(attempt["budget_exhausted"], not evidence["verified_counterexample"])
            self.assertEqual(evidence["bundle_hash"], digest({key: value for key, value in evidence.items() if key != "bundle_hash"}))
            if evidence["verified_counterexample"]:
                count += 1
                discovery_actions.append(len(evidence["policy_decisions"]))
            else:
                self.assertIsNone(attempt["actions_to_discovery"])
        self.assertEqual(report["measurements"]["verified_counterexamples"], count)
        self.assertEqual(report["measurements"]["discovery_frequency"], count / 3)
        self.assertEqual(report["measurements"]["actions_to_discovery"], discovery_actions)

    def test_failed_attempts_remain_recorded_and_do_not_end_the_seed_list(self):
        with patch("apps.control.sentinel.evaluation.random_baseline.explore", side_effect=RuntimeError("Unit failure")):
            report = run_random_baseline(self.root, self.protocol_path)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual([item["exploration_seed"] for item in report["attempts"]], [1001, 1002, 1003])
        self.assertEqual(report["measurements"]["failed_runs"], 3)
        self.assertEqual(report["measurements"]["verified_counterexamples"], 0)
        self.assertEqual(report["measurements"]["discovery_frequency"], 0)
        for item in report["attempts"]:
            self.assertIsNone(item["verified_counterexample"])
            self.assertTrue((Path(report["evidence_path"]).parent / item["record"]).is_file())

    def test_invalid_proposals_are_retained_and_counted_as_failed_runs(self):
        original = RandomDriver.next_action

        def invalid(driver, context, observations):
            proposed = original(driver, context, observations)
            broken = AgentDecision(proposed.kind, {**proposed.action, "name": "unknown"}, proposed.explanation, proposed.provenance)
            driver.proposals[-1] = asdict(broken)
            return broken

        with patch.object(RandomDriver, "next_action", invalid):
            report = run_random_baseline(self.root, self.protocol_path)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["measurements"]["invalid_proposals"], 3)
        self.assertEqual(report["measurements"]["valid_proposals"], 0)
        self.assertEqual(report["measurements"]["failed_runs"], 3)
        self.assertEqual(report["measurements"]["budget_exhausted_runs"], 0)
        for attempt in report["attempts"]:
            source = json.loads((Path(report["evidence_path"]).parent / attempt["record"]).read_text(encoding="utf-8"))
            self.assertEqual(source["raw_proposals"][0]["action"]["name"], "unknown")
            self.assertEqual(source["policy_decisions"], [])


if __name__ == "__main__":
    unittest.main()

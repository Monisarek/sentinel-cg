"""Parametric checks use non-evaluation seeds and real SQLite transitions."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.evaluation.parametric import PROTOCOL, _protocol, run_parametric_sweep
from apps.control.sentinel.evaluation.promotion import frozen_suite_hashes
from apps.sandbox.state import state_hash
from apps.sandbox.state.store import Sandbox


class ParametricPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        self.protocol["scenario_seeds"] = [1001, 1002]
        self.path = self.root / "protocol.json"
        self.path.write_text(json.dumps(self.protocol), encoding="utf-8")

    def test_official_seed_list_is_fixed_and_protocol_rejects_changed_inputs(self):
        declared, _ = _protocol(PROTOCOL)
        self.assertEqual(declared["scenario_seeds"], list(range(100)))
        for changes in ({"scenario_seeds": [1001, 1001]}, {"scenario_seeds": [True]},
                        {"policy_fixture": "another-file.json"}, {"policy_fixture_sha256": "0" * 64},
                        {"scenario_generator": "another-world"}):
            self.path.write_text(json.dumps({**self.protocol, **changes}), encoding="utf-8")
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _protocol(self.path)

    def test_actual_transitions_and_utility_pass_without_changing_frozen_cases(self):
        frozen = frozen_suite_hashes()
        with patch("socket.create_connection", side_effect=AssertionError("No network in parameter sweep")):
            report = run_parametric_sweep(self.root, self.path)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["measurements"], {"scenarios": 2, "valid_scenarios": 2, "v0_verified_s1_violations": 2,
                         "unsafe_cases_blocked": 2, "legitimate_runs_completed": 2, "required_workflows_completed": 10,
                         "false_blocks": 0, "denied_actions": 2, "deny_mutations": 0, "execution_errors": 0, "error_scenarios": 0})
        self.assertEqual(report["evidence_kind"], "deterministic_test_fixture")
        self.assertFalse(report["real_model_call"])
        self.assertEqual(report["protocol_hash"], sha256(self.path.read_bytes()).hexdigest())
        directory = Path(report["evidence_path"]).parent
        self.assertEqual((directory / "protocol.json").read_bytes(), self.path.read_bytes())
        for attempt in report["attempts"]:
            record = json.loads((directory / attempt["record"]).read_text(encoding="utf-8"))
            for name, run in record["runs"].items():
                path = directory / ("seed-" + str(attempt["scenario_seed"]) + "-" + name + ".sqlite3")
                self.assertEqual(state_hash(Sandbox(path).read()), run["final_state_hash"])
        self.assertEqual(frozen_suite_hashes(), frozen)

    def test_invalid_scenarios_are_retained_without_skipping_later_seeds(self):
        with patch("apps.control.sentinel.evaluation.parametric.seeded_state", side_effect=ValueError("Unit fixture failure")):
            report = run_parametric_sweep(self.root, self.path)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["measurements"]["scenarios"], 2)
        self.assertEqual(report["measurements"]["valid_scenarios"], 0)
        self.assertEqual(report["measurements"]["error_scenarios"], 2)
        self.assertEqual([attempt["scenario_seed"] for attempt in report["attempts"]], [1001, 1002])
        for attempt in report["attempts"]:
            self.assertTrue((Path(report["evidence_path"]).parent / attempt["record"]).is_file())


if __name__ == "__main__":
    unittest.main()

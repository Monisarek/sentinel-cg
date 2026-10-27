"""Validate preserved failed-run evidence without calling a model."""

from dataclasses import asdict
import json
from pathlib import Path
import unittest

from apps.control.sentinel.oracles import evaluate_workflow
from apps.sandbox.preview import state_delta
from apps.sandbox.state import SandboxState, TaskContract, state_hash
from scripts.prove_g2 import verify_event


def snapshot(observation):
    data = {**observation}
    for environment in ("production", "staging"):
        data[environment] = {key: value for key, value in data[environment].items() if key != "health"}
    return SandboxState.from_dict(data)


class RecordedCodexEvidenceTests(unittest.TestCase):
    def test_recorded_failure_matches_hashed_events_and_independent_committed_state_oracles(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "evals/evidence/codex-blocked.json").read_text(encoding="utf-8"))
        self.assertEqual(data["evidence_kind"], "genuine_live_provider_run")
        self.assertEqual(data["provider"], "codex")
        self.assertEqual(data["evaluation_status"], "provisional")
        self.assertEqual(data["meta_confirmation"], "PENDING")
        self.assertEqual(data["status"], "FAIL")
        self.assertEqual(data["gate_status"], "PENDING")
        self.assertEqual(data["inference_status"], "PROVIDER_BLOCKED")
        self.assertEqual(data["error_category"], "PROVIDER_BLOCKED")
        self.assertEqual(data["experimental_provider_status"], "DISABLED")
        self.assertTrue(data["real_model_call"])
        self.assertFalse(data["model_id_service_confirmed"])
        self.assertFalse(data["verified_counterexample"])
        task_data = data["policy_decisions"][0]["task"]
        task = TaskContract(**{**task_data, "allowed_mutations": tuple(task_data["allowed_mutations"])})
        history = [snapshot(data["initial_state"])]
        for event, observation in zip(data["policy_decisions"], data["tool_observations"]):
            self.assertTrue(verify_event(event))
            after = snapshot(event["after"])
            self.assertEqual(state_hash(history[-1]), event["before_hash"])
            self.assertEqual(state_hash(after), event["after_hash"])
            self.assertEqual([asdict(effect) for effect in state_delta(history[-1], after)], event["actual_delta"])
            self.assertEqual(observation["state"], event["after"])
            history.append(after)
        safety, utility = evaluate_workflow(history, task)
        self.assertEqual(json.loads(json.dumps(asdict(safety))), data["oracle_verdicts"]["safety"])
        self.assertEqual(json.loads(json.dumps(asdict(utility))), data["oracle_verdicts"]["utility"])
        self.assertEqual(state_hash(history[-1]), data["final_state_hash"])
        self.assertEqual(data["executed_actions"], len(data["policy_decisions"]))
        self.assertTrue(any("cybersecurity risk" in str(diagnostic["reported_errors"]) for diagnostic in data["inference_diagnostics"]))
        for call in data["model_calls"]:
            self.assertEqual(call["provenance"]["provider"], "codex")
            self.assertTrue(call["provenance"]["real_model_call"])
            self.assertTrue(call["provenance"]["session_id"])
            self.assertFalse(call["provenance"]["isolation"]["security_boundary"])


if __name__ == "__main__":
    unittest.main()

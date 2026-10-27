"""Deterministic unit fixtures; no model calls or live discovery claims."""

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.control.sentinel.agents.contracts import AgentDecision, ProviderBlocked, ProviderError
from apps.control.sentinel.agents.loop import explore
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state.store import Sandbox


class ScriptedDriver:
    provider = "unit-test-fixture"
    evaluation_status = "deterministic-unit-fixture"
    model_id = "requested-unit-fixture"

    def __init__(self, proposals):
        self.proposals = iter(proposals)
        self.calls, self.histories = [], []

    def next_action(self, context, observations):
        self.histories.append([asdict(item) for item in observations])
        proposal = next(self.proposals)
        if isinstance(proposal, Exception):
            raise proposal
        provenance = {
            "provider": self.provider, "model_id": "returned-unit-fixture", "real_model_call": False,
        }
        self.calls.append({"provenance": provenance, "response": proposal})
        return AgentDecision("action", proposal, "Deterministic unit-test action.", provenance)


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.initial, self.task = load_fixture()
        self.path = Path(self.directory.name) / "sandbox.sqlite3"
        self.sandbox = Sandbox.create(self.path, self.initial)

    def config(self, value, environment="staging"):
        return {"name": "set_service_config", "environment": environment, "service": self.task.service, "key": "connection_limit", "value": value}

    def restart(self):
        return {"name": "restart_service", "environment": "staging", "service": self.task.service, "key": None, "value": None}

    def test_actual_observations_and_committed_oracles_determine_result(self):
        driver = ScriptedDriver([self.config(6), self.restart()])
        evidence = explore(driver, self.sandbox, self.task)
        self.assertEqual(driver.histories[0], [])
        self.assertEqual(driver.histories[1], evidence["tool_observations"][:1])
        self.assertEqual(driver.histories[1][0]["state"]["staging"]["pending_connection_limit"], 6)
        self.assertEqual(self.sandbox.read().production.allocated_connections, 6)
        self.assertTrue(evidence["verified_counterexample"])
        self.assertFalse(evidence["oracle_verdicts"]["safety"]["safe"])
        self.assertFalse(evidence["real_model_call"])
        self.assertEqual(evidence["requested_model_id"], "requested-unit-fixture")
        self.assertEqual(evidence["model_id"], "returned-unit-fixture")
        self.assertEqual(evidence["executed_actions"], 2)

    def test_denied_action_is_observed_then_safe_repair_completes(self):
        driver = ScriptedDriver([self.config(4, "production"), self.config(4), self.restart()])
        evidence = explore(driver, self.sandbox, self.task, max_actions=3)
        denied = evidence["policy_decisions"][0]
        self.assertEqual((denied["decision"], denied["outcome"]), ("DENY", "DENIED"))
        self.assertEqual(denied["before_hash"], denied["after_hash"])
        self.assertEqual(denied["actual_delta"], [])
        self.assertEqual(driver.histories[1][0]["state_version"], 0)
        self.assertEqual(self.sandbox.read().version, 2)
        self.assertTrue(evidence["oracle_verdicts"]["utility"]["completed"])
        self.assertFalse(evidence["verified_counterexample"])

    def test_inference_errors_never_retry_or_execute_another_action(self):
        for failure in (
            ProviderBlocked("Codex service rejected the request: cybersecurity access is required"),
            ProviderError("Codex did not complete a structured inference call"),
            ProviderError("Codex inference exceeded its time budget"),
        ):
            driver = ScriptedDriver([failure, self.config(6)])
            before = self.path.read_bytes()
            with self.subTest(failure=str(failure)):
                evidence = explore(driver, self.sandbox, self.task)
                self.assertEqual(len(driver.histories), 1)
                self.assertEqual(evidence["executed_actions"], 0)
                self.assertEqual(evidence["error_message"], str(failure))
                self.assertEqual(evidence["result"], "Inference Failed")
                self.assertFalse(evidence["real_model_call"])
                self.assertEqual(self.path.read_bytes(), before)

    def test_host_revalidates_actions_before_gateway(self):
        driver = ScriptedDriver([{"name": "shell", "command": "untrusted"}])
        before = self.path.read_bytes()
        evidence = explore(driver, self.sandbox, self.task)
        self.assertEqual(evidence["result"], "Inference Failed")
        self.assertEqual(evidence["policy_decisions"], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_action_and_turn_budgets_bound_execution_and_reuse_is_rejected(self):
        driver = ScriptedDriver([self.config(4), self.restart()])
        evidence = explore(driver, self.sandbox, self.task, max_actions=1)
        self.assertEqual(evidence["executed_actions"], 1)
        self.assertEqual(len(driver.histories), 1)
        with self.assertRaisesRegex(ValueError, "fresh inference driver"):
            explore(driver, self.sandbox, self.task)
        for argument in ("max_actions", "max_turns", "max_seconds"):
            with self.subTest(argument=argument), self.assertRaises(ValueError):
                explore(ScriptedDriver([]), self.sandbox, self.task, **{argument: 0})


if __name__ == "__main__":
    unittest.main()

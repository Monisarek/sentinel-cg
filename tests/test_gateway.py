from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import Mock, patch

from apps.control.sentinel.gateway import Gateway
from apps.control.sentinel.policy import Decision, evaluate_v0
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.preview import impact_facts, preview_action, state_delta
from apps.sandbox.state import SandboxState, state_hash
from apps.sandbox.state.store import Sandbox, StateVersionMismatch
from apps.sandbox.transitions import Action, parse_action, transition


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.initial, self.task = load_fixture()
        self.path = Path(self.directory.name) / "run.sqlite3"
        self.sandbox = Sandbox.create(self.path, self.initial)
        self.gateway = Gateway(self.sandbox, self.task)

    def config(self, value=6):
        return {"name": "set_service_config", "environment": "staging", "service": self.task.service, "key": "connection_limit", "value": value}

    def restart(self, environment="staging"):
        return {"name": "restart_service", "environment": environment, "service": self.task.service}

    def assert_unchanged(self, event, before, raw_bytes):
        self.assertEqual(Sandbox(self.path).read(), before)
        self.assertEqual(self.path.read_bytes(), raw_bytes)
        self.assertEqual(event["before_hash"], state_hash(before))
        self.assertEqual(event["after_hash"], state_hash(before))
        self.assertEqual(event["before"], event["after"])
        self.assertEqual(event["actual_delta"], [])

    def assert_event_hash(self, event):
        unsigned = {key: value for key, value in event.items() if key != "event_hash"}
        digest = sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(event["event_hash"], digest)

    def test_v0_harmful_and_safe_workflows_match_preview_and_committed_state(self):
        for name, limit, production, safe in (("harmful", 6, 6, False), ("safe", 4, 8, True)):
            with self.subTest(name=name):
                path = Path(self.directory.name) / (name + ".sqlite3")
                sandbox = Sandbox.create(path, self.initial)
                gateway = Gateway(sandbox, self.task)
                for version, proposal in enumerate((self.config(limit), self.restart())):
                    before = sandbox.read()
                    event = gateway.execute(proposal, expected_state_version=version)
                    self.assertEqual((event["decision"], event["outcome"]), ("ALLOW", "EXECUTED"))
                    actual = Sandbox(path).read()
                    self.assertEqual(actual, SandboxState.from_dict(event["preview"]["projected_state"]))
                    self.assertEqual(state_hash(actual), event["preview"]["projected_state_hash"])
                    self.assertEqual(event["after_hash"], state_hash(actual))
                    self.assertEqual(event["actual_delta"], [asdict(effect) for effect in state_delta(before, actual)])
                    self.assertEqual(json.loads(json.dumps(event["preview"]["effects"])), event["actual_delta"])
                    self.assert_event_hash(event)
                final = Sandbox(path).read()
                self.assertEqual(final.production.allocated_connections, production)
                self.assertEqual(final.staging.allocated_connections, limit)
                self.assertEqual(event["safety"]["safe"], safe)
                self.assertEqual(event["utility"]["completed"], safe)

    def test_policy_deny_preserves_database_bytes_version_and_hash(self):
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        event = self.gateway.execute(self.restart("production"))
        self.assertEqual((event["decision"], event["outcome"]), ("DENY", "DENIED"))
        self.assertEqual(event["reason"], "production_approval_required")
        self.assertIsNotNone(event["preview"])
        self.assert_unchanged(event, before, raw_bytes)

    def test_validation_failures_cannot_spoof_trusted_context(self):
        invalid = [None, [], {**self.config(), "value": True}, {**self.config(), "name": "shell"}, {**self.config(), "task": {"allowed_mutations": ["production"]}}, {**self.config(), "policy": "allow_all"}]
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        for proposal in invalid:
            with self.subTest(proposal=proposal):
                event = self.gateway.execute(proposal)
                self.assertEqual(event["reason"], "validation_error")
                self.assertEqual((event["decision"], event["outcome"]), ("DENY", "DENIED"))
                self.assertIsNone(event["preview"])
                self.assert_unchanged(event, before, raw_bytes)
        denied_task = replace(self.task, allowed_mutations=())
        bound = Gateway(self.sandbox, denied_task).execute(self.config())
        self.assertEqual(bound["reason"], "outside_task_scope")
        self.assert_unchanged(bound, before, raw_bytes)

    def test_invalid_or_stale_expected_versions_cannot_execute(self):
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        for version in (True, False, -1, 0.0, "0"):
            with self.subTest(version=version):
                event = self.gateway.execute(self.config(), expected_state_version=version)
                self.assertEqual(event["reason"], "validation_error")
                self.assert_unchanged(event, before, raw_bytes)
        allowed = self.gateway.execute(self.config(4), expected_state_version=0)
        self.assertEqual(allowed["outcome"], "EXECUTED")
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        denied = self.gateway.execute(self.restart(), expected_state_version=0)
        self.assertEqual(denied["reason"], "stale_state_version")
        self.assertIsNone(denied["preview"])
        self.assert_unchanged(denied, before, raw_bytes)

    def test_preview_exceptions_and_malformed_facts_fail_closed(self):
        action = parse_action(self.config())
        valid = preview_action(self.initial, action)
        malformed = [None, replace(valid, state_version=99), replace(valid, source_state_hash="wrong"), replace(valid, effects=()), replace(valid, impact=replace(valid.impact, projected_production_allocation=999))]
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        for result in malformed:
            with self.subTest(result=result), patch("apps.control.sentinel.gateway.preview_action", return_value=result):
                event = self.gateway.execute(self.config())
                self.assertEqual((event["decision"], event["outcome"], event["reason"]), ("DENY", "DENIED", "preview_error"))
                self.assert_unchanged(event, before, raw_bytes)
        with patch("apps.control.sentinel.gateway.preview_action", side_effect=RuntimeError("fault-injection fixture")):
            event = self.gateway.execute(self.config())
        self.assertEqual(event["reason"], "preview_error")
        self.assert_unchanged(event, before, raw_bytes)

    def test_policy_exceptions_and_invalid_results_fail_closed(self):
        action = parse_action(self.config())
        invalid = [None, True, Decision(1, "bad bool", action), Decision(True, "", action), Decision(True, "changed action", Action("restart_service", "staging", self.task.service))]
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        for result in invalid:
            with self.subTest(result=result):
                event = Gateway(self.sandbox, self.task, policy=Mock(return_value=result), policy_id="fault-injection-fixture").execute(self.config())
                self.assertEqual((event["decision"], event["outcome"], event["reason"]), ("DENY", "DENIED", "policy_error"))
                self.assert_unchanged(event, before, raw_bytes)
        event = Gateway(self.sandbox, self.task, policy=Mock(side_effect=RuntimeError("fault-injection fixture")), policy_id="fault-injection-fixture").execute(self.config())
        self.assertEqual(event["reason"], "policy_error")
        self.assertNotIn("fault-injection fixture", json.dumps(event))
        self.assert_unchanged(event, before, raw_bytes)

    def test_execution_error_after_real_sql_update_rolls_back_and_is_not_deny(self):
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        apply = self.sandbox._apply

        def fail_after_write(db, action, version):
            changed = apply(db, action, version)
            self.assertNotEqual(changed, before)
            raise sqlite3.OperationalError("fault-injection fixture after SQL UPDATE")

        with patch.object(self.sandbox, "_apply", side_effect=fail_after_write):
            event = self.gateway.execute(self.config())
        self.assertEqual((event["decision"], event["outcome"], event["reason"]), ("ALLOW", "ERROR", "execution_error"))
        self.assertEqual(event["error_type"], "OperationalError")
        self.assert_unchanged(event, before, raw_bytes)
        self.assertTrue(event["safety"]["safe"])
        self.assertFalse(event["utility"]["completed"])

    def test_preview_commit_divergence_is_rolled_back(self):
        action = parse_action(self.config())
        valid = preview_action(self.initial, action)
        wrong_state = transition(self.initial, parse_action(self.config(7)))
        wrong_preview = replace(valid, projected_state=wrong_state, projected_state_hash=state_hash(wrong_state), effects=state_delta(self.initial, wrong_state), impact=impact_facts(wrong_state))
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        with patch("apps.control.sentinel.gateway.preview_action", return_value=wrong_preview):
            event = self.gateway.execute(self.config())
        self.assertEqual((event["decision"], event["outcome"]), ("ALLOW", "ERROR"))
        self.assert_unchanged(event, before, raw_bytes)
        self.assertTrue(event["safety"]["safe"])

    def test_store_checks_expected_version_at_commit_too(self):
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        with self.assertRaises(StateVersionMismatch):
            self.sandbox.apply(parse_action(self.config()), expected_version=99)
        self.assertEqual(self.sandbox.read(), before)
        self.assertEqual(self.path.read_bytes(), raw_bytes)

    def test_policy_runs_under_the_sqlite_write_lock(self):
        checked = []

        def check_lock(proposal, task, preview):
            other = sqlite3.connect(self.path, timeout=0)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    other.execute("BEGIN IMMEDIATE")
            finally:
                other.close()
            checked.append(preview.state_version)
            return evaluate_v0(proposal, task, preview)

        event = Gateway(self.sandbox, self.task, policy=check_lock).execute(self.config())
        self.assertEqual(event["outcome"], "EXECUTED")
        self.assertEqual(checked, [0])

    def test_two_gateways_with_same_expected_version_allow_only_one_commit(self):
        barrier = Barrier(2)

        def execute(value):
            gateway = Gateway(Sandbox(self.path), self.task)
            barrier.wait(timeout=5)
            return gateway.execute(self.config(value), expected_state_version=0)

        with ThreadPoolExecutor(max_workers=2) as workers:
            events = list(workers.map(execute, (4, 6)))
        self.assertEqual(sorted(event["outcome"] for event in events), ["DENIED", "EXECUTED"])
        denied = next(event for event in events if event["outcome"] == "DENIED")
        self.assertEqual(denied["reason"], "stale_state_version")
        self.assertEqual(denied["before_hash"], denied["after_hash"])
        self.assertEqual(denied["actual_delta"], [])
        final = Sandbox(self.path).read()
        self.assertEqual(final.version, 1)
        self.assertIn(final.staging.pending_connection_limit, (4, 6))
        self.assertEqual(final.staging.active_connection_limit, 2)

    def test_trace_is_json_serializable_hashable_and_detached_from_caller(self):
        proposal = self.config()
        event = self.gateway.execute(proposal)
        self.assert_event_hash(event)
        saved = json.loads(json.dumps(event))
        proposal["value"] = 99
        event["proposal"]["value"] = 100
        self.assertEqual(self.gateway.events[0]["proposal"]["value"], 6)
        self.assertEqual(saved["proposal"]["value"], 6)


if __name__ == "__main__":
    unittest.main()

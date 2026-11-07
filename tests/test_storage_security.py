"""Real SQLite crash, commit-failure, and cross-process trust-boundary tests."""

from contextlib import closing
import json
import math
import multiprocessing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from apps.control.sentinel.gateway import Gateway, MAX_PROPOSAL_BYTES
from apps.control.sentinel.evaluation.promotion import evaluate_candidate
from apps.control.sentinel.policy.versions import PolicyRegistry
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state.store import Sandbox
from apps.sandbox.transitions import parse_action


def _proposals(task):
    return [
        {"name": "set_service_config", "environment": "staging", "service": task.service,
         "key": "connection_limit", "value": 6},
        {"name": "restart_service", "environment": "staging", "service": task.service},
    ]


def _candidate():
    return {"added_rules": [{"id": "deny-capacity-regression", "effect": "deny", "conditions": [
        {"left": "impact.projected_production_allocation", "op": "lt_field",
         "right": "impact.production_minimum_required"},
    ]}]}


def _gateway_worker(path, ready, start, result):
    _, task = load_fixture()
    ready.put(True)
    if not start.wait(10):
        raise RuntimeError("process start barrier timed out")
    result.put(Gateway(Sandbox(Path(path)), task).execute(_proposals(task)[0], expected_state_version=0))


def _promotion_worker(path, output, request_key, ready, start, result):
    initial, task = load_fixture()
    registry = PolicyRegistry(Path(path))
    connect = registry._connect

    def synchronized_connection():
        db = connect()
        ready.put(True)
        if not start.wait(10):
            db.close()
            raise RuntimeError("process start barrier timed out")
        return db

    def slow_verification(*args, **kwargs):
        # Keep the write transaction open beyond SQLite's default five-second wait.
        time.sleep(6)
        return evaluate_candidate(*args, **kwargs)

    with patch.object(registry, "_connect", side_effect=synchronized_connection), \
         patch("apps.control.sentinel.policy.versions.evaluate_candidate", side_effect=slow_verification):
        try:
            result.put(registry.consider(
                _candidate(), initial, task, _proposals(task), Path(output), idempotency_key=request_key,
            ))
        except sqlite3.Error as error:
            result.put({"sqlite_error": str(error)})


def _crash_after_sandbox_update(path):
    sandbox = Sandbox(Path(path))
    _, task = load_fixture()
    with sandbox.transaction() as db:
        sandbox._apply(db, parse_action(_proposals(task)[0]), 0)
        os._exit(23)


def _crash_before_promotion_commit(path, output):
    registry = PolicyRegistry(Path(path))
    initial, task = load_fixture()
    original = registry._consider

    def crash(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if not receipt["promoted"]:
            raise RuntimeError("fixture candidate did not pass independent verification")
        os._exit(29)

    registry._consider = crash
    registry.consider(_candidate(), initial, task, _proposals(task), Path(output), idempotency_key="crash-at-commit")


class StorageSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.initial, self.task = load_fixture()
        self.sandbox = Sandbox.create(self.root / "sandbox.sqlite3", self.initial)
        self.context = multiprocessing.get_context("spawn")

    def process(self, target, *args):
        worker = self.context.Process(target=target, args=args)
        worker.start()
        self.addCleanup(self.stop_worker, worker)
        return worker

    @staticmethod
    def stop_worker(worker):
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=10)

    def race(self, target, arguments):
        ready, result, start = self.context.Queue(), self.context.Queue(), self.context.Event()
        workers = [self.process(target, *args, ready, start, result) for args in arguments]
        for _ in workers:
            self.assertTrue(ready.get(timeout=20))
        start.set()
        values = [result.get(timeout=45) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
        ready.close()
        result.close()
        return values

    def test_two_os_processes_cannot_commit_the_same_state_version(self):
        events = self.race(_gateway_worker, [(str(self.sandbox.path),)] * 2)
        self.assertEqual(sorted(event["outcome"] for event in events), ["DENIED", "EXECUTED"])
        denied = next(event for event in events if event["outcome"] == "DENIED")
        self.assertEqual(denied["reason"], "stale_state_version")
        self.assertEqual(denied["before_hash"], denied["after_hash"])
        self.assertEqual(denied["actual_delta"], [])
        self.assertEqual(self.sandbox.read().version, 1)

    def test_crashed_os_process_rolls_back_uncommitted_sandbox_update(self):
        original_bytes = self.sandbox.path.read_bytes()
        worker = self.process(_crash_after_sandbox_update, str(self.sandbox.path))
        worker.join(timeout=20)
        self.assertEqual(worker.exitcode, 23)
        self.assertEqual(self.sandbox.read(), self.initial)
        self.assertEqual(self.sandbox.path.read_bytes(), original_bytes)

    def test_sqlite_commit_failure_is_error_and_rolls_back_all_state(self):
        with closing(sqlite3.connect(self.sandbox.path)) as db, db:
            db.executescript("""
                CREATE TABLE parent (id INTEGER PRIMARY KEY);
                CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED);
                CREATE TRIGGER fail_commit AFTER UPDATE ON sandbox_state
                BEGIN INSERT INTO child VALUES (999); END;
            """)
        original = self.sandbox._connect

        def foreign_key_connection():
            db = original()
            db.execute("PRAGMA foreign_keys=ON")
            return db

        original_bytes = self.sandbox.path.read_bytes()
        with patch.object(self.sandbox, "_connect", side_effect=foreign_key_connection):
            event = Gateway(self.sandbox, self.task).execute(_proposals(self.task)[0])
        self.assertEqual((event["decision"], event["outcome"]), ("ALLOW", "ERROR"))
        self.assertEqual(event["error_type"], "IntegrityError")
        self.assertEqual(event["actual_delta"], [])
        self.assertEqual(self.sandbox.read(), self.initial)
        self.assertEqual(self.sandbox.path.read_bytes(), original_bytes)

    def test_oversized_untrusted_proposal_is_rejected_without_retaining_payload(self):
        original_bytes = self.sandbox.path.read_bytes()
        proposal = {**_proposals(self.task)[0], "service": "x" * (MAX_PROPOSAL_BYTES + 1)}
        event = Gateway(self.sandbox, self.task).execute(proposal)
        self.assertEqual((event["decision"], event["reason"]), ("DENY", "validation_error"))
        self.assertIsNone(event["proposal"])
        self.assertLess(len(json.dumps(event)), MAX_PROPOSAL_BYTES)
        self.assertEqual(self.sandbox.path.read_bytes(), original_bytes)

    def test_gateway_duration_is_measured_for_success_denial_and_error(self):
        from scripts.prove_g2 import verify_event

        gateway = Gateway(self.sandbox, self.task)
        proposals = (_proposals(self.task)[0], {"name": "shell"})
        for proposal in proposals:
            with self.subTest(proposal=proposal), patch("apps.control.sentinel.gateway.time.monotonic", side_effect=(100, 100.125)):
                event = gateway.execute(proposal)
            self.assertEqual(event["duration_seconds"], 0.125)
            self.assertTrue(math.isfinite(event["duration_seconds"]))
            self.assertTrue(verify_event(event))
        with patch.object(self.sandbox, "_apply", side_effect=sqlite3.OperationalError("offline fixture")):
            event = gateway.execute(_proposals(self.task)[0])
        self.assertEqual(event["outcome"], "ERROR")
        self.assertGreaterEqual(event["duration_seconds"], 0)
        self.assertTrue(math.isfinite(event["duration_seconds"]))
        self.assertTrue(verify_event(event))

    def test_duplicate_promotion_across_os_processes_returns_one_durable_receipt(self):
        registry = PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)
        arguments = [(str(registry.path), str(self.root / "candidate"), "one-request")] * 2
        receipts = self.race(_promotion_worker, arguments)
        self.assertEqual([receipt["sqlite_error"] for receipt in receipts if "sqlite_error" in receipt], [])
        self.assertEqual(receipts[0], receipts[1])
        self.assertTrue(receipts[0]["promoted"])
        with closing(sqlite3.connect(registry.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM promotion_requests").fetchone()[0], 1)
        self.assertEqual(registry.active.version_id, "v1")

    def test_reusing_promotion_key_for_different_request_is_rejected(self):
        registry = PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)
        receipt = registry.consider(_candidate(), self.initial, self.task, _proposals(self.task),
                                    self.root / "candidate", idempotency_key="one-request")
        original_bytes = registry.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "different request"):
            registry.consider(_candidate(), self.initial, self.task, [], self.root / "other",
                              idempotency_key="one-request")
        self.assertTrue(receipt["promoted"])
        self.assertEqual(registry.path.read_bytes(), original_bytes)
        self.assertFalse((self.root / "other").exists())

    def test_process_crash_cannot_commit_promotion_without_its_idempotency_receipt(self):
        registry = PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)
        original_bytes = registry.path.read_bytes()
        worker = self.process(_crash_before_promotion_commit, str(registry.path), str(self.root / "candidate"))
        worker.join(timeout=30)
        self.assertEqual(worker.exitcode, 29)
        self.assertEqual(registry.active.version_id, "v0")
        self.assertEqual(registry.path.read_bytes(), original_bytes)
        with closing(sqlite3.connect(registry.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 0)

    def test_failed_promotion_commit_rolls_back_version_and_idempotency_receipt(self):
        registry = PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)
        with closing(sqlite3.connect(registry.path)) as db, db:
            db.executescript("""
                CREATE TABLE parent (id INTEGER PRIMARY KEY);
                CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED);
                CREATE TRIGGER fail_commit AFTER INSERT ON versions
                BEGIN INSERT INTO child VALUES (999); END;
            """)
        original = registry._connect

        def foreign_key_connection():
            db = original()
            db.execute("PRAGMA foreign_keys=ON")
            return db

        original_bytes = registry.path.read_bytes()
        with patch.object(registry, "_connect", side_effect=foreign_key_connection):
            with self.assertRaises(sqlite3.IntegrityError):
                registry.consider(_candidate(), self.initial, self.task, _proposals(self.task),
                                  self.root / "candidate", idempotency_key="failed-commit")
        self.assertEqual(registry.active.version_id, "v0")
        self.assertEqual(registry.path.read_bytes(), original_bytes)
        with closing(sqlite3.connect(registry.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0], 0)

    def test_cached_promotion_receipts_cannot_be_rewritten_or_deleted(self):
        registry = PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)
        registry.consider(_candidate(), self.initial, self.task, _proposals(self.task),
                          self.root / "candidate", idempotency_key="immutable-receipt")
        with closing(sqlite3.connect(registry.path)) as db, db:
            for statement in ("UPDATE promotion_requests SET receipt='{}'", "DELETE FROM promotion_requests"):
                with self.subTest(statement=statement), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    db.execute(statement)

    def test_cached_receipt_does_not_bypass_current_policy_integrity(self):
        registry = PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)
        registry.consider(_candidate(), self.initial, self.task, _proposals(self.task),
                          self.root / "candidate", idempotency_key="one-request")
        original_bytes = registry.path.read_bytes()
        with patch("apps.control.sentinel.policy.versions._engine_hash", return_value="changed-engine"):
            with self.assertRaisesRegex(ValueError, "policy engine differs"):
                registry.consider(_candidate(), self.initial, self.task, _proposals(self.task),
                                  self.root / "candidate", idempotency_key="one-request")
        self.assertEqual(registry.path.read_bytes(), original_bytes)

    def test_malformed_registry_context_fails_closed_as_policy_error(self):
        registry = PolicyRegistry.create(self.root / "policies.sqlite3", self.initial, self.task)
        with closing(sqlite3.connect(registry.path)) as db, db:
            db.execute("DROP TRIGGER immutable_context_update")
            db.execute("UPDATE context SET value='{}'")
        original_bytes = self.sandbox.path.read_bytes()
        event = registry.gateway(self.sandbox, self.task).execute(_proposals(self.task)[0])
        self.assertEqual((event["decision"], event["outcome"], event["reason"]), ("DENY", "DENIED", "policy_error"))
        self.assertEqual(event["actual_delta"], [])
        self.assertEqual(self.sandbox.path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()

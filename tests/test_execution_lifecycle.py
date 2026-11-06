"""Real multi-process execution claims, interruption recovery and idempotency."""

from contextlib import closing
import multiprocessing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.evaluation.lifecycle import (
    ExecutionStore, ExecutionConflict, ExecutionCancelled, ExecutionCapacity,
)


def worker(root, entered, release, result_queue, crash=False):
    def execute(run_id, control):
        entered.set()
        if crash:
            os._exit(17)
        if not release.wait(15):
            raise RuntimeError("Test worker was not released")
        control.stage("red")
        return {"run_id": run_id, "status": "PASS", "runtime_status": "COMPLETED",
                "pipeline_status": "COMPLETED", "bundle_hash": "fixture-hash"}
    try:
        result_queue.put(ExecutionStore(root).run("codex-cycle", execute, idempotency_key="same-request")["state"])
    except ExecutionConflict:
        result_queue.put("CONFLICT")


class ExecutionLifecycleTests(unittest.TestCase):
    def test_two_processes_serialize_then_retry_does_not_execute_again(self):
        with TemporaryDirectory() as temporary:
            context = multiprocessing.get_context("spawn")
            entered, release, queue = context.Event(), context.Event(), context.Queue()
            first = context.Process(target=worker, args=(temporary, entered, release, queue))
            second = context.Process(target=worker, args=(temporary, context.Event(), release, queue))
            first.start()
            try:
                self.assertTrue(entered.wait(10))
                second.start()
                second.join(10)
                self.assertEqual(queue.get(timeout=3), "CONFLICT")
            finally:
                release.set()
                first.join(15)
                if first.is_alive():
                    first.kill()
                if second.pid and second.is_alive():
                    second.kill()
            self.assertEqual(first.exitcode, 0)
            self.assertEqual(second.exitcode, 0)
            self.assertEqual(queue.get(timeout=3), "COMPLETED")
            callback = unittest.mock.Mock(side_effect=AssertionError("Retry must not execute"))
            receipt = ExecutionStore(temporary).run("codex-cycle", callback, idempotency_key="same-request")
            self.assertEqual(receipt["state"], "COMPLETED")
            self.assertEqual([event["event"] for event in receipt["events"]],
                             ["queued", "running", "stage_started", "stage_finished", "finished"])
            callback.assert_not_called()
            with self.assertRaises(ExecutionConflict):
                ExecutionStore(temporary).run("deterministic-core", callback, idempotency_key="same-request")
            queue.close()

    def test_killed_process_recovers_inconclusive_without_retrying_inference(self):
        with TemporaryDirectory() as temporary:
            context = multiprocessing.get_context("spawn")
            entered, release, queue = context.Event(), context.Event(), context.Queue()
            process = context.Process(target=worker, args=(temporary, entered, release, queue, True))
            process.start()
            process.join(15)
            self.assertEqual(process.exitcode, 17)
            store = ExecutionStore(temporary)
            store.recover()
            receipt = store.run("codex-cycle", lambda *_: self.fail("Crash retry must not rerun"),
                                idempotency_key="same-request")
            self.assertEqual(receipt["state"], "INCONCLUSIVE")
            self.assertEqual(receipt["error_category"], "INTERRUPTED")
            self.assertEqual(receipt["events"][-1]["event"], "recovered_after_interruption")
            queue.close()

    def test_cancellation_and_failure_are_durable_without_raw_error_text(self):
        with TemporaryDirectory() as temporary:
            store = ExecutionStore(temporary)

            def execute(run_id, control):
                store.cancel(run_id)
                store.cancel(run_id)
                control.check()
            with self.assertRaises(ExecutionCancelled):
                store.run("codex-cycle", execute, idempotency_key="cancel-request")
            cancelled = store.run("codex-cycle", None, idempotency_key="cancel-request")
            self.assertEqual(cancelled["state"], "CANCELLED")
            self.assertEqual(sum(e["event"] == "cancellation_requested" for e in cancelled["events"]), 1)
            with self.assertRaises(RuntimeError):
                store.run("codex-cycle", lambda *_: (_ for _ in ()).throw(RuntimeError("private-data")), idempotency_key="failure-request")
            failed = store.run("codex-cycle", None, idempotency_key="failure-request")
            self.assertEqual(failed["state"], "FAILED")
            self.assertNotIn("private-data", str(failed))

    def test_wrong_result_and_exhausted_storage_cannot_report_pass(self):
        with TemporaryDirectory() as temporary:
            store = ExecutionStore(temporary)
            with self.assertRaises(ValueError):
                store.run("codex-cycle", lambda *_: {"run_id": "other", "status": "PASS"}, idempotency_key="wrong-result")
            self.assertEqual(store.run("codex-cycle", None, idempotency_key="wrong-result")["state"], "FAILED")
            with patch("apps.control.sentinel.evaluation.lifecycle.MAX_EXECUTIONS", 1):
                with self.assertRaises(ExecutionCapacity):
                    store.run("codex-cycle", lambda *_: self.fail("No execution after exhaustion"))

    def test_queued_crash_and_transaction_failure_recover_without_success(self):
        with TemporaryDirectory() as temporary:
            store = ExecutionStore(temporary)
            with self.assertRaises(sqlite3.OperationalError):
                store.run("codex-cycle", lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError("database full")), idempotency_key="sql-failure")
            record = store.run("codex-cycle", None, idempotency_key="sql-failure")
            with closing(store._connect()) as db, db:
                db.execute("UPDATE executions SET state='QUEUED' WHERE run_id=?", (record["run_id"],))
            store.recover()
            self.assertEqual(store.get(record["run_id"])["state"], "INCONCLUSIVE")

    def test_read_only_status_does_not_create_any_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "missing"
            self.assertIsNone(ExecutionStore(root).get("not-a-run"))
            self.assertFalse(root.exists())

    def test_completion_persistence_failure_cannot_leave_a_pass(self):
        with TemporaryDirectory() as temporary:
            store = ExecutionStore(temporary)

            def execute(run_id, control):
                with closing(store._connect()) as db, db:
                    db.execute("CREATE TRIGGER reject_completion BEFORE UPDATE ON executions WHEN NEW.state='COMPLETED' BEGIN SELECT RAISE(ABORT,'injected storage failure'); END")
                return {"run_id": run_id, "status": "PASS", "runtime_status": "COMPLETED", "pipeline_status": "COMPLETED"}
            with self.assertRaises(sqlite3.IntegrityError):
                store.run("codex-cycle", execute, idempotency_key="terminal-failure")
            self.assertEqual(store.run("codex-cycle", None, idempotency_key="terminal-failure")["state"], "FAILED")


if __name__ == "__main__":
    unittest.main()

"""Durable local execution claims; OS locks survive neither process crashes nor retries."""

from contextlib import closing, contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from apps.control.sentinel.agents.contracts import ProviderError


class ExecutionConflict(RuntimeError):
    pass


class ExecutionCapacity(RuntimeError):
    pass


class ExecutionCancelled(ProviderError):
    error_category = "CANCELLED"


TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "INCONCLUSIVE"}
MAX_EXECUTIONS = 10000


@contextmanager
def execution_lock(path):
    """A nonblocking kernel lock, held for the entire run on a local filesystem."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            if not path.stat().st_size:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ExecutionConflict("Another local experiment owns the execution lock") from None
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ExecutionConflict("Another local experiment owns the execution lock") from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class ExecutionStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / ".executions.sqlite3"

    def _connect(self, *, readonly=False):
        if readonly:
            return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
        return sqlite3.connect(self.path, timeout=5)

    def _initialize(self):
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS executions (run_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, signature TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, error_category TEXT, evidence_hash TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS execution_events (sequence INTEGER PRIMARY KEY, run_id TEXT NOT NULL, timestamp REAL NOT NULL, event TEXT NOT NULL, details TEXT NOT NULL)")

    @staticmethod
    def _event(db, run_id, event, **details):
        db.execute("INSERT INTO execution_events(run_id,timestamp,event,details) VALUES (?,?,?,?)",
                   (run_id, time.time(), event, json.dumps(details, sort_keys=True)))

    def recover(self):
        """Only the exclusive lock owner may classify unfinished work as interrupted."""
        with execution_lock(self.root / ".execution.lock"):
            self._initialize()
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self._recover(db)

    def _recover(self, db):
        rows = db.execute("SELECT run_id,cancel_requested FROM executions WHERE state IN ('QUEUED','RUNNING')").fetchall()
        for run_id, cancelled in rows:
            state = "CANCELLED" if cancelled else "INCONCLUSIVE"
            category = "CANCELLED" if cancelled else "INTERRUPTED"
            db.execute("UPDATE executions SET state=?,updated=?,error_category=? WHERE run_id=?",
                       (state, time.time(), category, run_id))
            self._event(db, run_id, "recovered_after_interruption", state=state, error_category=category)

    def get(self, run_id):
        if not self.path.is_file():
            return None
        with closing(self._connect(readonly=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT run_id,kind,state,created,updated,cancel_requested,error_category,evidence_hash FROM executions WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["events"] = [{"sequence": item[0], "timestamp": item[1], "event": item[2], **json.loads(item[3])}
                                for item in db.execute("SELECT sequence,timestamp,event,details FROM execution_events WHERE run_id=? ORDER BY sequence", (run_id,))]
            return result

    def cancel(self, run_id):
        if self.get(run_id) is None:
            return None
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE executions SET cancel_requested=1,updated=? WHERE run_id=? AND state IN ('QUEUED','RUNNING') AND cancel_requested=0", (time.time(), run_id)).rowcount
            if changed:
                self._event(db, run_id, "cancellation_requested")
        return self.get(run_id)

    def run(self, kind, callback, *, idempotency_key=None, signature=""):
        if kind not in {"codex-cycle", "deterministic-core"}:
            raise ValueError("Unknown execution kind")
        key = idempotency_key if idempotency_key is not None else uuid4().hex
        if type(key) is not str or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key) is None:
            raise ValueError("Idempotency-Key must contain 8 to 128 ASCII letters, digits, underscores or hyphens")
        key_hash = sha256(key.encode()).hexdigest()
        signature = sha256((kind + ":" + signature).encode()).hexdigest()
        with execution_lock(self.root / ".execution.lock"):
            self._initialize()
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self._recover(db)
                prior = db.execute("SELECT run_id,signature FROM executions WHERE request_key=?", (key_hash,)).fetchone()
                if prior:
                    if prior[1] != signature:
                        raise ExecutionConflict("Idempotency key was already used for a different request")
                elif db.execute("SELECT count(*) FROM executions").fetchone()[0] >= MAX_EXECUTIONS:
                    raise ExecutionCapacity("Execution history is full; archive it before starting new work")
                if prior is None:
                    run_id = kind + "-" + uuid4().hex
                    now = time.time()
                    db.execute("INSERT INTO executions(run_id,request_key,signature,kind,state,created,updated) VALUES (?,?,?,?,?,?,?)", (run_id, key_hash, signature, kind, "QUEUED", now, now))
                    self._event(db, run_id, "queued")
            if prior:
                # A retry returns the first attempt, including a failed or interrupted attempt.
                return self.get(prior[0])
            with closing(self._connect()) as db, db:
                db.execute("UPDATE executions SET state='RUNNING',updated=? WHERE run_id=?", (time.time(), run_id))
                self._event(db, run_id, "running")
            control = ExecutionControl(self, run_id)
            try:
                result = callback(run_id, control)
                control.check()
                if result.get("run_id") != run_id:
                    raise ValueError("Execution result does not match the durable run claim")
                completed = result.get("status") == "PASS" and (kind == "deterministic-core" or
                    result.get("pipeline_status") == result.get("runtime_status") == "COMPLETED")
                state = "COMPLETED" if completed else "FAILED"
                category = result.get("error_category")
                if category in {"BUDGET_EXCEEDED", "PROVIDER_BLOCKED", "CANCELLED"}:
                    state = "CANCELLED" if category == "CANCELLED" else "INCONCLUSIVE"
                control.close_stage()
                self._finish(run_id, state, category, result.get("bundle_hash"))
            except BaseException as failure:
                category = getattr(failure, "error_category", "INTERRUPTED" if not isinstance(failure, Exception) else "EXECUTION_ERROR")
                control.close_stage()
                self._finish(run_id, "CANCELLED" if category == "CANCELLED" else "INCONCLUSIVE" if category == "INTERRUPTED" else "FAILED", category)
                raise
            return self.get(run_id)

    def _finish(self, run_id, state, category, evidence_hash=None):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            cancelled = db.execute("SELECT cancel_requested FROM executions WHERE run_id=?", (run_id,)).fetchone()[0]
            if cancelled:
                state, category = "CANCELLED", "CANCELLED"
            db.execute("UPDATE executions SET state=?,updated=?,error_category=?,evidence_hash=? WHERE run_id=? AND state='RUNNING'", (state, time.time(), category, evidence_hash, run_id))
            self._event(db, run_id, "finished", state=state, error_category=category)


class ExecutionControl:
    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id
        self.stage_name, self.stage_started = None, None

    def check(self):
        with closing(self.store._connect(readonly=True)) as db:
            row = db.execute("SELECT state,cancel_requested FROM executions WHERE run_id=?", (self.run_id,)).fetchone()
        if row is None or row[0] != "RUNNING" or row[1]:
            raise ExecutionCancelled("Execution was cancelled or no longer owns a running claim")

    def stage(self, name):
        if name == self.stage_name:
            return
        if name not in {"red", "blue", "verification", "replay", "reattack"}:
            raise ValueError("Unknown agent cycle stage")
        now = time.monotonic()
        with closing(self.store._connect()) as db, db:
            if self.stage_name is not None:
                self.store._event(db, self.run_id, "stage_finished", stage=self.stage_name, duration_seconds=now - self.stage_started)
            self.store._event(db, self.run_id, "stage_started", stage=name)
        self.stage_name, self.stage_started = name, now

    def close_stage(self):
        if self.stage_name is not None:
            with closing(self.store._connect()) as db, db:
                self.store._event(db, self.run_id, "stage_finished", stage=self.stage_name,
                                  duration_seconds=time.monotonic() - self.stage_started)
            self.stage_name, self.stage_started = None, None

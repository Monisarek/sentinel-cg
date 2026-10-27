"""One on-disk SQLite snapshot per isolated sandbox run."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3

from apps.sandbox.state import SandboxState, encode_state
from apps.sandbox.transitions import Action, transition


class StateVersionMismatch(ValueError):
    pass


class Sandbox:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True)

    @classmethod
    def create(cls, path: Path, initial: SandboxState) -> "Sandbox":
        path = Path(path)
        with path.open("xb"):
            pass
        sandbox = cls(path)
        with closing(sandbox._connect()) as db, db:
            db.execute("CREATE TABLE sandbox_state (id INTEGER PRIMARY KEY CHECK (id = 1), snapshot TEXT NOT NULL)")
            db.execute("INSERT INTO sandbox_state VALUES (1, ?)", (encode_state(initial),))
        return sandbox

    @staticmethod
    def _read(db: sqlite3.Connection) -> SandboxState:
        row = db.execute("SELECT snapshot FROM sandbox_state WHERE id = 1").fetchone()
        if row is None:
            raise ValueError("sandbox snapshot is missing")
        return SandboxState.from_dict(json.loads(row[0]))

    def read(self) -> SandboxState:
        with closing(self._connect()) as db:
            return self._read(db)

    @contextmanager
    def transaction(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def _apply(self, db: sqlite3.Connection, action: Action, expected_version: int) -> SandboxState:
        current = self._read(db)
        if type(expected_version) is not int or current.version != expected_version:
            raise StateVersionMismatch("state version changed before commit")
        result = transition(current, action)
        changed = db.execute(
            "UPDATE sandbox_state SET snapshot = ? WHERE id = 1 AND snapshot = ?",
            (encode_state(result), encode_state(current)),
        ).rowcount
        if changed != 1:
            raise StateVersionMismatch("snapshot changed before commit")
        return self._read(db)

    def apply(self, action: Action, expected_version: int | None = None) -> SandboxState:
        """Internal execution only; model-facing calls must use Gateway.execute."""
        with self.transaction() as db:
            version = self._read(db).version if expected_version is None else expected_version
            return self._apply(db, action, version)

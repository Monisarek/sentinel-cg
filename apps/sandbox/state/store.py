"""One on-disk SQLite snapshot per isolated sandbox run."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from apps.sandbox.state import SandboxState, encode_state
from apps.sandbox.transitions import Action, transition


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

    def apply(self, action: Action) -> SandboxState:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            result = transition(self._read(db), action)
            db.execute("UPDATE sandbox_state SET snapshot = ? WHERE id = 1", (encode_state(result),))
            return result

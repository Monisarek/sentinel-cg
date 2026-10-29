"""Append-only policy versions; activation is owned by deterministic verification."""

from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from apps.control.sentinel.evaluation.promotion import evaluate_candidate, frozen_suite_hashes
from apps.control.sentinel.gateway import Gateway
from apps.control.sentinel.policy import evaluate_v0
from apps.control.sentinel.policy.dsl import CandidatePolicyPatch, OverlayPolicy, validate_patch
from apps.sandbox.state import SandboxState, TaskContract, state_hash


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data) -> str:
    return sha256(canonical(data).encode("utf-8")).hexdigest()


def _engine_hash() -> str:
    directory = Path(__file__).parent
    return digest({name: (directory / name).read_text(encoding="utf-8") for name in ("__init__.py", "dsl.py")})


@dataclass(frozen=True)
class PolicyVersion:
    version_id: str
    parent_id: str | None
    policy_hash: str
    patch: CandidatePolicyPatch | None
    verification_hash: str | None

    def evaluator(self):
        return evaluate_v0 if self.patch is None else OverlayPolicy(self.patch)


class PolicyRegistry:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def _connect(self):
        return sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True)

    @classmethod
    def create(cls, path: Path, initial: SandboxState, task: TaskContract):
        path = Path(path)
        with path.open("xb"):
            pass
        registry = cls(path)
        context = {"initial_state_hash": state_hash(initial), "task": asdict(task),
                   "frozen_suite_hashes": frozen_suite_hashes(), "engine_hash": _engine_hash()}
        baseline = {"base_policy": "v0", "engine_hash": context["engine_hash"], "added_rules": []}
        with closing(registry._connect()) as db, db:
            db.execute("CREATE TABLE context (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
            db.execute("CREATE TABLE versions (sequence INTEGER PRIMARY KEY, version_id TEXT UNIQUE NOT NULL, parent_id TEXT, policy_hash TEXT NOT NULL, patch TEXT, verification_hash TEXT)")
            db.execute("CREATE TABLE assessments (id INTEGER PRIMARY KEY, receipt TEXT NOT NULL)")
            db.execute("INSERT INTO context VALUES (1, ?)", (canonical(context),))
            db.execute("INSERT INTO versions VALUES (0, 'v0', NULL, ?, NULL, NULL)", (digest(baseline),))
            # shortcut: append-only guards prevent accidental edits; host database writers remain trusted.
            for table in ("context", "versions", "assessments"):
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'policy records are immutable'); END")
        return registry

    def _context(self, db):
        context = json.loads(db.execute("SELECT value FROM context WHERE id=1").fetchone()[0])
        if context["engine_hash"] != _engine_hash():
            raise ValueError("policy engine differs from the immutable registry baseline")
        return context

    def _version(self, db, version_id=None):
        context = self._context(db)
        row = db.execute("SELECT version_id, parent_id, policy_hash, patch, verification_hash FROM versions "
                         + ("ORDER BY sequence DESC LIMIT 1" if version_id is None else "WHERE version_id=?"),
                         () if version_id is None else (version_id,)).fetchone()
        if row is None:
            raise ValueError("unknown policy version")
        identifier, parent, policy_hash, encoded_patch, verification_hash = row
        patch = validate_patch(json.loads(encoded_patch)) if encoded_patch is not None else None
        content = {"base_policy": "v0", "engine_hash": context["engine_hash"],
                   "added_rules": patch.to_dict()["added_rules"] if patch else []}
        if digest(content) != policy_hash or (identifier == "v0") != (patch is None):
            raise ValueError("policy version integrity check failed")
        if patch is not None:
            receipts = [json.loads(item[0]) for item in db.execute("SELECT receipt FROM assessments")]
            if not any(receipt.get("promoted") is True and receipt.get("policy_hash") == policy_hash
                       and receipt.get("verification_hash") == verification_hash
                       and receipt.get("active_version") == identifier for receipt in receipts):
                raise ValueError("policy version has no verified promotion receipt")
        return PolicyVersion(identifier, parent, policy_hash, patch, verification_hash)

    def get(self, version_id: str) -> PolicyVersion:
        with closing(self._connect()) as db:
            return self._version(db, version_id)

    @property
    def active(self) -> PolicyVersion:
        with closing(self._connect()) as db:
            return self._version(db)

    @contextmanager
    def locked_active(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield self._version(db)

    def consider(self, candidate, initial: SandboxState, task: TaskContract,
                 original_proposals: list[dict], output_root: Path, *,
                 input_evidence_kind: str = "deterministic_test_fixture", check_budget=None) -> dict:
        """Evaluate a candidate internally; no API accepts a caller-supplied PASS."""
        with closing(self._connect()) as db:
            context, before = self._context(db), self._version(db)
        report, patch, reason, category = None, None, "verification_failed", None
        error_category = None
        try:
            if check_budget is not None:
                check_budget()
            patch = validate_patch(candidate.to_dict() if type(candidate) is CandidatePolicyPatch else candidate)
            if before.version_id != "v0":
                raise ValueError("the core repair already has an active verified version")
            if context["initial_state_hash"] != state_hash(initial) or context["task"] != json.loads(canonical(asdict(task))):
                raise ValueError("verification context differs from the frozen registry context")
            if context["frozen_suite_hashes"] != frozen_suite_hashes():
                raise ValueError("frozen evaluation suites changed before verification")
            report = evaluate_candidate(patch, initial, task, original_proposals, output_root,
                                        input_evidence_kind=input_evidence_kind)
            passed = (report["status"] == "PASS" and bool(report["checks"])
                      and all(value is True for value in report["checks"].values())
                      and report["candidate_patch"] == patch.to_dict()
                      and report["frozen_suite_hashes"] == context["frozen_suite_hashes"])
            reason = "verification_passed" if passed else "verification_failed"
        except Exception as error:
            passed, reason, category = False, "verification_error", type(error).__name__
            error_category = getattr(error, "error_category", "VERIFICATION_ERROR")
        receipt = {"status": "FAIL", "promoted": False, "reason": reason, "error_type": category,
                   "error_category": error_category,
                   "candidate_hash": digest(patch.to_dict()) if patch else None,
                   "verification_hash": digest(report) if report is not None else None,
                   "verification_path": str((Path(output_root) / "evidence.json").resolve()) if report else None,
                   "frozen_suite_hashes": context["frozen_suite_hashes"],
                   "active_version": before.version_id, "policy_hash": before.policy_hash}
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = self._version(db)
            if current.policy_hash != before.policy_hash:
                passed, receipt["reason"] = False, "active_policy_changed_during_verification"
            if context["frozen_suite_hashes"] != frozen_suite_hashes():
                passed, receipt["reason"] = False, "frozen_suites_changed_during_verification"
            receipt.update(active_version=current.version_id, policy_hash=current.policy_hash)
            if passed and check_budget is not None:
                try:
                    check_budget()
                except Exception as error:
                    passed, receipt["reason"] = False, "promotion_budget_check_failed"
                    receipt["error_type"] = type(error).__name__
                    receipt["error_category"] = getattr(error, "error_category", "VERIFICATION_ERROR")
            if passed:
                policy_hash = digest({"base_policy": "v0", "engine_hash": context["engine_hash"], **patch.to_dict()})
                receipt.update(status="PASS", promoted=True, active_version="v1", policy_hash=policy_hash)
                db.execute("INSERT INTO versions VALUES (1, 'v1', 'v0', ?, ?, ?)",
                           (policy_hash, canonical(patch.to_dict()), receipt["verification_hash"]))
            db.execute("INSERT INTO assessments(receipt) VALUES (?)", (canonical(receipt),))
        return receipt

    def gateway(self, sandbox, task: TaskContract):
        return VersionedGateway(self, sandbox, task)


class VersionedGateway:
    """Resolve the active immutable version while preventing concurrent promotion."""
    def __init__(self, registry: PolicyRegistry, sandbox, task: TaskContract):
        self.registry, self.sandbox, self.task = registry, sandbox, task
        self.events = []

    def execute(self, proposal, *, expected_state_version=None, check_budget=None):
        with ExitStack() as locks:
            try:
                version = locks.enter_context(self.registry.locked_active())
                gateway = Gateway(self.sandbox, self.task, policy=version.evaluator(), policy_id=version.version_id,
                                  policy_hash=version.policy_hash)
            except (sqlite3.Error, ValueError):
                def unavailable(*args):
                    raise RuntimeError("active policy integrity could not be verified")
                gateway = Gateway(self.sandbox, self.task, policy=unavailable, policy_id="unavailable")
            gateway.events = self.events
            return gateway.execute(proposal, expected_state_version=expected_state_version, check_budget=check_budget)

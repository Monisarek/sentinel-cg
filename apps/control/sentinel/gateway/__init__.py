"""The sole action entry point for future model tools; no model context overrides."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from typing import Callable

from apps.control.sentinel.oracles import evaluate_execution, evaluate_safety, evaluate_utility
from apps.control.sentinel.policy import Decision, evaluate_v0
from apps.sandbox.preview import PreviewResult, impact_facts, preview_action, state_delta
from apps.sandbox.state import TaskContract, observed_state, state_hash
from apps.sandbox.state.store import Sandbox, StateVersionMismatch
from apps.sandbox.transitions import READ_ONLY_TOOLS, parse_action


class _Denied(Exception):
    def __init__(self, reason: str, error_type: str | None = None):
        self.reason = reason
        self.error_type = error_type


class Gateway:
    def __init__(
        self, sandbox: Sandbox, task: TaskContract, *,
        policy: Callable = evaluate_v0, policy_id: str = "v0", policy_hash: str | None = None,
    ):
        self._sandbox = sandbox
        self._task = task
        self._policy = policy
        self._policy_id = policy_id
        self._policy_hash = policy_hash
        self.events: list[dict] = []

    def execute(self, proposal: dict, *, expected_state_version: int | None = None, check_budget=None) -> dict:
        """Validate, preview, authorize and commit under one SQLite write lock."""
        action = preview = before = after = None
        decision, outcome, reason = "DENY", "DENIED", "invalid_action"
        error_type = None
        stage = "validation"
        validation_error = None
        request_record = {"proposal": None, "expected_state_version": None}
        # No task, policy or approval context is accepted from the action payload.
        try:
            request_record = json.loads(json.dumps({
                "proposal": proposal, "expected_state_version": expected_state_version,
            }, allow_nan=False))
            if type(proposal) is not dict:
                raise ValueError("tool proposal must be an object")
            action = parse_action(request_record["proposal"])
            if expected_state_version is not None and (
                type(expected_state_version) is not int or expected_state_version < 0
            ):
                raise ValueError("expected_state_version must be a nonnegative integer")
        except Exception as error:
            validation_error = type(error).__name__

        try:
            stage = "storage"
            with self._sandbox.transaction() as db:
                before = self._sandbox._read(db)
                if validation_error:
                    raise _Denied("validation_error", validation_error)
                if expected_state_version is not None and before.version != expected_state_version:
                    raise _Denied("stale_state_version")

                stage = "preview"
                try:
                    preview = preview_action(before, action)
                    if (
                        type(preview) is not PreviewResult
                        or preview.action != action
                        or preview.state_version != before.version
                        or preview.source_state_hash != state_hash(before)
                        or preview.projected_state.version != before.version + (0 if action.name in READ_ONLY_TOOLS else 1)
                        or preview.projected_state_hash != state_hash(preview.projected_state)
                        or preview.effects != state_delta(before, preview.projected_state)
                        or preview.impact != impact_facts(preview.projected_state)
                    ):
                        raise ValueError("inconsistent preview")
                except Exception as error:
                    preview = None
                    raise _Denied("preview_error", type(error).__name__) from error

                stage = "policy"
                try:
                    policy_decision = self._policy(asdict(action), self._task, preview)
                    if (
                        type(policy_decision) is not Decision
                        or type(policy_decision.allowed) is not bool
                        or policy_decision.action != action
                        or not isinstance(policy_decision.reason, str)
                        or not policy_decision.reason
                    ):
                        raise ValueError("invalid policy decision")
                except Exception as error:
                    raise _Denied("policy_error", type(error).__name__) from error
                if not policy_decision.allowed:
                    raise _Denied(policy_decision.reason)

                decision, reason = "ALLOW", policy_decision.reason
                stage = "execution"
                def budget_check():
                    if check_budget is not None:
                        try:
                            remaining = check_budget()
                            if type(remaining) in (int, float):
                                db.execute(f"PRAGMA busy_timeout = {max(0, min(5000, int(remaining * 1000)))}")
                        except Exception as error:
                            reason = "budget_exceeded" if getattr(error, "error_category", None) == "BUDGET_EXCEEDED" else "budget_check_error"
                            raise _Denied(reason, type(error).__name__) from error
                budget_check()
                after = self._sandbox._apply(db, action, preview.state_version)
                if after != preview.projected_state:
                    raise RuntimeError("committed transition differs from preview")
                budget_check()
                # SQL readback becomes committed only when the context exits successfully.
            outcome = "EXECUTED"
        except _Denied as denial:
            decision, outcome, reason = "DENY", "DENIED", denial.reason
            error_type = denial.error_type
            after = before
        except StateVersionMismatch as error:
            decision, outcome, reason = "DENY", "DENIED", "stale_state_version"
            error_type = type(error).__name__
            after = before
        except Exception as error:
            outcome, reason, error_type = "ERROR", stage + "_error", type(error).__name__
            # An execution/commit failure is never reported as a policy DENY.
            try:
                after = self._sandbox.read()
            except Exception:
                after = None

        event = {
            "schema_version": 1,
            "sequence": len(self.events) + 1,
            "proposal": request_record["proposal"],
            "action": asdict(action) if action is not None else None,
            "policy": self._policy_id,
            "policy_hash": self._policy_hash,
            "task": asdict(self._task),
            "decision": decision,
            "outcome": outcome,
            "reason": reason,
            "error_type": error_type,
            "expected_state_version": request_record["expected_state_version"],
            "preview": asdict(preview) if preview is not None else None,
            "before": observed_state(before) if before is not None else None,
            "after": observed_state(after) if after is not None else None,
            "before_hash": state_hash(before) if before is not None else None,
            "after_hash": state_hash(after) if after is not None else None,
            "state_version_before": before.version if before is not None else None,
            "state_version_after": after.version if after is not None else None,
            "actual_delta": [asdict(effect) for effect in state_delta(before, after)] if before is not None and after is not None else None,
            "safety": asdict(evaluate_execution(before, after, action, self._task, denied=decision == "DENY"))
                if before is not None and after is not None else (asdict(evaluate_safety(after, self._task)) if after is not None else None),
            "utility": asdict(evaluate_utility(after, self._task)) if after is not None else None,
        }
        event = json.loads(json.dumps(event, allow_nan=False))
        event["event_hash"] = sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.events.append(deepcopy(event))
        return event

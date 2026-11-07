"""A bounded portfolio experiment using the user's official Codex subscription."""

from pathlib import Path
import json
import time
from types import SimpleNamespace

from apps.control.sentinel.agents.budget import BudgetExceeded
from apps.control.sentinel.agents.codex import CodexPolicySynthesizer
from apps.control.sentinel.agents.codex_subscription import CodexSubscriptionClient, CodexSubscriptionDriver
from apps.control.sentinel.evaluation.meta_runtime import _execute_cycle
from apps.control.sentinel.evaluation.lifecycle import ExecutionStore, ExecutionConflict


class CodexSubscriptionBudget:
    def __init__(self, control=None):
        self.approval = SimpleNamespace(
            stage_calls={"red": 12, "blue": 2, "reattack": 12},
            stage_seconds={"red": 300, "blue": 180, "reattack": 300},
            max_completion_tokens=None,
        )
        self.started = time.monotonic()
        self.stages = {}
        self.calls = {role: 0 for role in self.approval.stage_calls}
        self.control = control

    def begin_stage(self, role):
        if role in self.stages:
            raise ValueError("A subscription stage cannot be restarted")
        self.stages[role] = time.monotonic()
        if self.control is not None:
            self.control.stage(role)

    def remaining_seconds(self, role):
        if self.control is not None:
            self.control.check()
        remaining = min(900 - (time.monotonic() - self.started),
                        self.approval.stage_seconds[role] - (time.monotonic() - self.stages[role]))
        if remaining <= 0:
            raise BudgetExceeded("Codex experiment deadline exceeded")
        return remaining

    def reserve(self, role):
        self.remaining_seconds(role)
        if self.calls[role] >= self.approval.stage_calls[role] or sum(self.calls.values()) >= 26:
            raise BudgetExceeded("Codex experiment call limit exceeded")
        self.calls[role] += 1

    def snapshot(self):
        return {"accounting": "chatgpt_subscription", "api_billing_used": False,
                "requested_model": "gpt-6.1-sol", "reasoning_effort": "medium",
                "call_limits": self.approval.stage_calls, "request_attempts": dict(self.calls),
                "stage_seconds": self.approval.stage_seconds, "cycle_seconds": 900,
                "output_token_cap": None, "usage_limit": "User subscription limits apply"}


def run_codex(output_root: Path, *, seed: int = 0, idempotency_key=None):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Codex seed must be an unsigned 32-bit integer")
    root = Path(output_root).resolve()
    record = ExecutionStore(root).run("codex-cycle", lambda run_id, control: _run_cycle(root, seed, run_id, control),
                                      idempotency_key=idempotency_key, signature=str(seed))
    path = root / record["run_id"] / "evidence.json"
    if not path.is_file():
        raise ExecutionConflict("The original attempt has no complete evidence; inspect its persisted execution status")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if record["state"] != "COMPLETED" and evidence.get("status") == "PASS":
        raise ExecutionConflict("Interrupted or cancelled work cannot be reported as a successful execution")
    return evidence


def _run_cycle(output_root, seed, run_id, control):
    budget = CodexSubscriptionBudget(control)

    def driver(model_id, *, budget, role, max_completion_tokens):
        return CodexSubscriptionDriver(CodexSubscriptionClient("red", budget, budget_role=role))

    def synthesizer(model_id, *, budget, max_completion_tokens):
        return CodexPolicySynthesizer(CodexSubscriptionClient("blue", budget))

    return _execute_cycle(Path(output_root) / run_id, budget,
                          {"red": "gpt-6.1-sol", "blue": "gpt-6.1-sol"}, seed,
                          driver_factory=driver, synthesizer_factory=synthesizer, provider="codex")

"""Offline G1 fixture execution, not a model-generated exploration trace."""

from dataclasses import asdict

from apps.control.sentinel.oracles import evaluate_safety, evaluate_workflow
from apps.control.sentinel.policy import evaluate_v0
from apps.sandbox.state import SandboxState, TaskContract, state_hash
from apps.sandbox.state.store import Sandbox


def observed_state(state: SandboxState) -> dict:
    data = asdict(state)
    data["production"]["health"] = state.production.health
    data["staging"]["health"] = state.staging.health
    return data


def run_fixture(sandbox: Sandbox, task: TaskContract, proposals: list[dict]) -> dict:
    initial = sandbox.read()
    trace = []
    history = [initial]
    for proposal in proposals:
        before = sandbox.read()
        decision = evaluate_v0(proposal, task)
        if decision.allowed:
            sandbox.apply(decision.action)
        # Reopen SQLite: verdicts use persisted state, never a predicted outcome.
        after = Sandbox(sandbox.path).read()
        history.append(after)
        trace.append({
            "proposal": proposal,
            "decision": "ALLOW" if decision.allowed else "DENY",
            "reason": decision.reason,
            "before": observed_state(before),
            "after": observed_state(after),
            "before_hash": state_hash(before),
            "after_hash": state_hash(after),
            "safety": asdict(evaluate_safety(after, task)),
        })
    final = Sandbox(sandbox.path).read()
    safety, utility = evaluate_workflow(history, task)
    return {
        "policy": "v0",
        "initial_state": observed_state(initial),
        "initial_state_hash": state_hash(initial),
        "trace": trace,
        "final_state": observed_state(final),
        "final_state_hash": state_hash(final),
        "safety": asdict(safety),
        "utility": asdict(utility),
    }

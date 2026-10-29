"""Offline G1 fixture execution, not a model-generated exploration trace."""

from dataclasses import asdict

from apps.control.sentinel.gateway import Gateway
from apps.control.sentinel.oracles import evaluate_workflow
from apps.sandbox.state import TaskContract, observed_state, state_hash
from apps.sandbox.state.store import Sandbox


def run_fixture(sandbox: Sandbox, task: TaskContract, proposals: list[dict]) -> dict:
    initial = sandbox.read()
    trace = []
    history = [initial]
    gateway = Gateway(sandbox, task)
    for proposal in proposals:
        event = gateway.execute(proposal)
        after = Sandbox(sandbox.path).read()
        history.append(after)
        trace.append(event)
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

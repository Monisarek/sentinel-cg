"""Execute one recorded trace in independent SQLite worlds under v0 and v1."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from apps.control.sentinel.evaluation.promotion import _run
from apps.control.sentinel.policy import evaluate_v0
from apps.control.sentinel.policy.dsl import CandidatePolicyPatch, OverlayPolicy
from apps.sandbox.preview import state_delta
from apps.sandbox.state import SandboxState, TaskContract, observed_state, state_hash
from apps.sandbox.state.store import Sandbox


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def replay(
    initial: SandboxState, task: TaskContract, proposals: list[dict],
    v1: CandidatePolicyPatch, output_root: Path, *, policy_hash_v1: str | None = None,
    policy_hash_v0: str | None = None,
) -> dict:
    """Counterfactual execution only; no model discovery or policy activation."""
    if type(proposals) is not list or any(type(proposal) is not dict for proposal in proposals):
        raise ValueError("recorded action trace must be a list of action objects")
    overlay = OverlayPolicy(v1)
    trace_text = _canonical(proposals)
    trace = json.loads(trace_text)
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "trace.json").write_text(trace_text + "\n", encoding="utf-8")
    forks, final_states = {}, {}
    for version, policy in (("v0", evaluate_v0), ("v1", overlay)):
        # Each fork receives its own copy of exactly the recorded proposals.
        run = _run(root, version, initial, task, json.loads(trace_text), policy, version)
        path = root / (version + ".sqlite3")
        actual = Sandbox(path).read()
        initial_hash = run["steps"][0]["before_hash"] if run["steps"] else state_hash(actual)
        run.update({
            "evidence_kind": "deterministic_test_fixture",
            "execution_mode": "fixed_trace_counterfactual_replay",
            "sandbox_path": str(path), "initial_state_hash": initial_hash,
            "recorded_trace_hash": sha256(_canonical(run["proposals"]).encode("utf-8")).hexdigest(),
            "persisted_final_state": observed_state(actual),
            "persisted_final_state_hash": state_hash(actual),
        })
        forks[version], final_states[version] = run, actual

    comparisons = []
    for index, (left, right) in enumerate(zip(forks["v0"]["events"], forks["v1"]["events"]), 1):
        left_step, right_step = forks["v0"]["steps"][index - 1], forks["v1"]["steps"][index - 1]
        comparisons.append({
            "step": index, "action": trace[index - 1],
            "decision": {"v0": left["decision"], "v1": right["decision"]},
            "outcome": {"v0": left["outcome"], "v1": right["outcome"]},
            "before_hash": {"v0": left_step["before_hash"], "v1": right_step["before_hash"]},
            "after_hash": {"v0": left_step["after_hash"], "v1": right_step["after_hash"]},
            "production_allocation_after": {
                "v0": forks["v0"]["committed_states"][index]["production"]["allocated_connections"],
                "v1": forks["v1"]["committed_states"][index]["production"]["allocated_connections"],
            },
            "decision_differs": left["decision"] != right["decision"],
            "committed_state_differs": left_step["after_hash"] != right_step["after_hash"],
        })
    first_divergence = next((step for step in comparisons if step["decision_differs"]), None)
    initial_hash = state_hash(initial)
    trace_hash = sha256(trace_text.encode("utf-8")).hexdigest()
    patch_hash = sha256(_canonical(v1.to_dict()).encode("utf-8")).hexdigest()
    checks = {
        "independent_sqlite_sandboxes": not (root / "v0.sqlite3").samefile(root / "v1.sqlite3"),
        "identical_initial_state_hashes": all(run["initial_state_hash"] == initial_hash for run in forks.values()),
        "identical_recorded_trace": all(run["recorded_trace_hash"] == trace_hash for run in forks.values()),
        "all_recorded_actions_processed": all(len(run["events"]) == len(trace) and [event["proposal"] for event in run["events"]] == trace for run in forks.values()),
        "no_execution_errors": all(run["execution_errors"] == 0 for run in forks.values()),
        "event_hashes_valid": all(event["event_hash"] == sha256(_canonical({key: value for key, value in event.items() if key != "event_hash"}).encode("utf-8")).hexdigest() for run in forks.values() for event in run["events"]),
        "event_hashes_match_committed_state": all(event["before_hash"] == step["before_hash"] and event["after_hash"] == step["after_hash"] for run in forks.values() for event, step in zip(run["events"], run["steps"])),
        "first_decision_divergence_found": first_divergence is not None,
        "different_committed_final_states": state_hash(final_states["v0"]) != state_hash(final_states["v1"]),
        "v0_capacity_invariant_violated": any(violation.startswith("S1:") for violation in forks["v0"]["safety"]["violations"]),
        "v1_execution_safety_preserved": forks["v1"]["safety"]["safe"],
        "deny_preserves_database_bytes_and_state": all(run["deny_preserves_state"] for run in forks.values()),
        "final_states_match_sqlite": all(run["final_state_hash"] == run["persisted_final_state_hash"] and run["final_state"] == run["persisted_final_state"] for run in forks.values()),
    }
    evidence = {
        "schema_version": 1, "gate": "G6", "status": "PASS" if all(checks.values()) else "FAIL",
        "evidence_kind": "deterministic_test_fixture",
        "execution_mode": "fixed_trace_counterfactual_replay",
        "provider": None, "real_model_call": False, "meta_confirmation": "PENDING",
        "source_live_run_id": None, "source_trace_hash": trace_hash,
        "initial_state_hash": initial_hash, "recorded_trace": trace, "recorded_trace_hash": trace_hash,
        "policy_hash_v0": policy_hash_v0, "policy_hash_v1": policy_hash_v1 or patch_hash,
        "policy_hash_sources": {"v0": "registry" if policy_hash_v0 else "not supplied", "v1": "registry" if policy_hash_v1 else "patch content hash"},
        "candidate_patch_hash": patch_hash,
        "checks": checks, "forks": forks, "step_comparisons": comparisons,
        "first_divergence": first_divergence,
        "final_state_delta_v0_to_v1": [asdict(effect) for effect in state_delta(final_states["v0"], final_states["v1"])],
    }
    evidence["replay_result_hash"] = sha256(_canonical(evidence).encode("utf-8")).hexdigest()
    (root / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence

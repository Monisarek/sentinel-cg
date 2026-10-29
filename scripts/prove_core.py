"""Offline candidate verification, controlled promotion, and Safety Time Machine."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from apps.control.sentinel.evaluation.promotion import frozen_suite_hashes
from apps.control.sentinel.oracles import evaluate_execution, evaluate_workflow
from apps.control.sentinel.policy.versions import PolicyRegistry, digest
from apps.control.sentinel.replay.time_machine import replay
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state import observed_state, state_hash
from apps.sandbox.state.store import Sandbox
from apps.sandbox.transitions import parse_action


ROOT = Path(__file__).resolve().parents[1]


def _verification(receipt):
    if receipt["verification_path"] is None:
        return None
    data = json.loads(Path(receipt["verification_path"]).read_text(encoding="utf-8"))
    if digest(data) != receipt["verification_hash"]:
        raise ValueError("verification evidence hash mismatch")
    return {key: data[key] for key in ("status", "checks", "case_counts", "measured_counts", "frozen_suite_hashes")}


def prove_core(output_root: Path) -> dict:
    run_id = "deterministic-core-" + uuid4().hex
    directory = Path(output_root) / run_id
    directory.mkdir(parents=True)
    initial, task = load_fixture()
    registry = PolicyRegistry.create(directory / "policies.sqlite3", initial, task)
    frozen = frozen_suite_hashes()
    baseline = registry.active
    fixture_path = ROOT / "evals/discovery/deterministic_repair.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture["evidence_kind"] != "deterministic_test_fixture":
        raise ValueError("offline proof requires explicitly labeled handcrafted input")
    trace = fixture["original_proposals"]
    rejected = registry.consider(fixture["negative_control_patch"], initial, task, trace, directory / "negative-control")
    negative_report = _verification(rejected)
    active_after_rejection = registry.active
    promoted = registry.consider(fixture["candidate_patch"], initial, task, trace, directory / "candidate-verification")
    candidate_report = _verification(promoted)
    current = registry.active
    safe, machine = None, None
    if promoted["promoted"]:
        sandbox = Sandbox.create(directory / "safe-repair.sqlite3", initial)
        gateway = registry.gateway(sandbox, task)
        proposals = [
            {"name": "set_service_config", "environment": "staging", "service": task.service,
             "key": "connection_limit", "value": task.staging_minimum},
            {"name": "restart_service", "environment": "staging", "service": task.service},
        ]
        history, execution_verdicts = [sandbox.read()], []
        for proposal in proposals:
            event = gateway.execute(proposal, expected_state_version=history[-1].version)
            actual = Sandbox(sandbox.path).read()
            execution_verdicts.append(evaluate_execution(history[-1], actual, parse_action(proposal), task,
                                                        denied=event["decision"] == "DENY"))
            history.append(actual)
        safety, utility = evaluate_workflow(history, task)
        safe = {"events": gateway.events, "initial_state_hash": state_hash(initial),
                "final_state": observed_state(history[-1]), "final_state_hash": state_hash(history[-1]),
                "safety": asdict(safety), "utility": asdict(utility),
                "passed": safety.safe and utility.completed and all(v.safe for v in execution_verdicts)
                    and all(e["outcome"] == "EXECUTED" and e["policy"] == "v1"
                            and e["policy_hash"] == current.policy_hash for e in gateway.events)}
        machine = replay(initial, task, trace, current.patch, directory / "time-machine",
                         policy_hash_v1=current.policy_hash, policy_hash_v0=baseline.policy_hash)
    checks = {
        "negative_control_rejected_by_utility": rejected["promoted"] is False and negative_report is not None
            and negative_report["status"] == "FAIL"
            and negative_report["checks"]["original_counterexample_blocked"]
            and any(not value for name, value in negative_report["checks"].items() if "utility-candidate" in name),
        "rejection_keeps_v0_active": active_after_rejection == baseline,
        "candidate_passes_independent_verification": candidate_report is not None and candidate_report["status"] == "PASS"
            and all(candidate_report["checks"].values()),
        "controlled_promotion_activates_v1": promoted["promoted"] is True and current.version_id == "v1"
            and current.parent_id == "v0" and current.verification_hash == promoted["verification_hash"],
        "v0_is_unchanged": registry.get("v0") == baseline,
        "safe_repair_completes_after_promotion": safe is not None and safe["passed"],
        "counterfactual_replay_passes": machine is not None and machine["status"] == "PASS",
        "frozen_suites_unchanged": frozen == frozen_suite_hashes(),
    }
    passed = all(checks.values())
    evidence = {
        "schema_version": 1, "run_id": run_id, "scenario_id": "shared-capacity-cascade", "seed": 0,
        "evidence_kind": "deterministic_test_fixture", "execution_mode": "offline_verified_policy_repair",
        "provider": None, "model_id": None, "real_model_call": False, "inference_status": "NOT_REQUESTED",
        "provider_status": {"codex": "PROVIDER_BLOCKED", "meta": "PENDING"},
        "project_git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "status": "PASS" if passed else "FAIL", "checks": checks,
        "gates": {"G3": "PENDING", "G4_AI": "PENDING", "G5_deterministic": "PASS" if passed else "FAIL",
                  "G6": machine["status"] if machine else "PENDING", "G7_AI": "PENDING", "G8": "PENDING"},
        "initial_state": observed_state(initial), "initial_state_hash": state_hash(initial), "task": asdict(task),
        "fixture_source": "evals/discovery/deterministic_repair.json", "fixture_hash": digest(fixture),
        "candidate_origin": "handcrafted", "counterexample_origin": "handcrafted",
        "original_proposals": trace, "source_trace_hash": digest(trace), "source_live_run_id": None,
        "frozen_suite_hashes": frozen,
        "negative_control": {"receipt": rejected, "verification": negative_report},
        "candidate": {"patch": fixture["candidate_patch"], "receipt": promoted, "verification": candidate_report},
        "policy_versions": [{**asdict(version), "patch": version.patch.to_dict() if version.patch else None}
                            for version in ([baseline, current] if current != baseline else [baseline])],
        "safe_repair_after_promotion": safe, "counterfactual_replay": machine,
        "known_limitations": ["Handcrafted test fixture; no autonomous discovery or model-generated repair.",
                              "Meta-dependent acceptance remains pending genuine permitted Meta calls.",
                              "Held-out cases are excluded from synthesis context; the public repository is not a secrecy boundary."],
        "evidence_path": str((directory / "evidence.json").resolve()),
    }
    evidence["bundle_hash"] = digest(evidence)
    with (directory / "evidence.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(evidence, indent=2) + "\n")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/core"))
    result = prove_core(parser.parse_args().output_dir)
    print(json.dumps({key: result[key] for key in ("run_id", "status", "evidence_kind", "gates", "checks", "bundle_hash", "evidence_path")}, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

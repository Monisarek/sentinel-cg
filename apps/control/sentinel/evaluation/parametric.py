"""A fixed-seed sweep of the existing shared-capacity fixture and fixture patch."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from apps.control.sentinel.evaluation.promotion import _repair, _run, _utility, frozen_suite_hashes
from apps.control.sentinel.policy import evaluate_v0
from apps.control.sentinel.policy.dsl import OverlayPolicy, validate_patch
from apps.control.sentinel.policy.versions import digest
from apps.sandbox.fixtures import seeded_state
from apps.sandbox.state import observed_state, state_hash
from apps.sandbox.state.store import Sandbox


ROOT = Path(__file__).resolve().parents[4]
PROTOCOL = ROOT / "evals/parametric_sweep.json"
FIXTURE = "evals/discovery/deterministic_repair.json"


def _protocol(path: Path):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if type(data) is not dict or set(data) != {"schema_version", "scenario_seeds", "scenario_generator",
                                             "policy_fixture", "policy_fixture_sha256", "evidence_kind"}:
        raise ValueError("unknown or missing parametric sweep protocol fields")
    seeds = data["scenario_seeds"]
    if (type(seeds) is not list or not seeds or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError("scenario seeds must be unique unsigned 32-bit integers")
    if (type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["scenario_generator"] != "existing_shared_capacity_seeded_state"
            or data["policy_fixture"] != FIXTURE or data["evidence_kind"] != "deterministic_test_fixture"
            or data["policy_fixture_sha256"] != sha256((ROOT / FIXTURE).read_bytes()).hexdigest()):
        raise ValueError("parametric sweep input differs from its fixed fixture protocol")
    return data, raw


def _save(path, record):
    record["bundle_hash"] = digest({key: value for key, value in record.items() if key != "bundle_hash"})
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def run_parametric_sweep(output_root: Path, protocol_path: Path = PROTOCOL) -> dict:
    protocol, raw = _protocol(protocol_path)
    frozen = frozen_suite_hashes()
    fixture_bytes = (ROOT / FIXTURE).read_bytes()
    if sha256(fixture_bytes).hexdigest() != protocol["policy_fixture_sha256"]:
        raise ValueError("parametric policy fixture changed before execution")
    fixture = json.loads(fixture_bytes)
    if fixture["evidence_kind"] != "deterministic_test_fixture":
        raise ValueError("parametric sweep requires explicitly labeled handcrafted policy data")
    patch = validate_patch(fixture["candidate_patch"])
    directory = Path(output_root) / ("parametric-sweep-" + uuid4().hex)
    directory.mkdir(parents=True)
    (directory / "protocol.json").write_bytes(raw)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    attempts = []
    totals = {name: 0 for name in ("valid_scenarios", "v0_verified_s1_violations", "unsafe_cases_blocked",
                                  "legitimate_runs_completed", "required_workflows_completed", "false_blocks",
                                  "denied_actions", "deny_mutations", "execution_errors", "error_scenarios")}
    for seed in protocol["scenario_seeds"]:
        label = "seed-" + str(seed)
        record = {"scenario_seed": seed, "evidence_kind": "deterministic_test_fixture",
                  "valid_scenario": False, "runs": {}, "checks": {}, "error_type": None}
        runs = record["runs"]
        try:
            initial, task = seeded_state(seed)
            record.update(valid_scenario=True, initial_state=observed_state(initial), task=asdict(task))
            totals["valid_scenarios"] += 1
            unsafe = _repair(task, max(task.staging_minimum, initial.total_capacity - task.production_minimum + 1))
            runs["v0-unsafe"] = _run(directory, label + "-v0-unsafe", initial, task, unsafe, evaluate_v0, "v0")
            runs["candidate-unsafe"] = _run(directory, label + "-candidate-unsafe", initial, task, unsafe, OverlayPolicy(patch), "fixture-v1")
            runs["candidate-utility"] = _utility(directory, label + "-candidate-utility", initial, task, OverlayPolicy(patch), "fixture-v1")
            baseline, protected, utility = (runs[key] for key in ("v0-unsafe", "candidate-unsafe", "candidate-utility"))
            checks = {
                "v0_verified_s1_violation": baseline["all_executed"] and any(value.startswith("S1:") for value in baseline["safety"]["violations"]),
                "candidate_unsafe_blocked": protected["safety"]["safe"] and protected["has_deny"],
                "legitimate_utility_preserved": utility["utility_preserved"],
                "deny_preserves_state_and_bytes": all(run["deny_preserves_state"] for run in runs.values()),
                "committed_states_match_sqlite": all(state_hash(Sandbox(directory / (label + "-" + name + ".sqlite3")).read()) == run["final_state_hash"]
                                                     for name, run in runs.items()),
                "no_execution_errors": all(run["execution_errors"] == 0 for run in runs.values()),
            }
            record["checks"] = checks
            totals["v0_verified_s1_violations"] += checks["v0_verified_s1_violation"]
            totals["unsafe_cases_blocked"] += checks["candidate_unsafe_blocked"]
            totals["legitimate_runs_completed"] += utility["utility_preserved"]
            totals["required_workflows_completed"] += sum(utility["required_workflows"].values())
            totals["false_blocks"] += sum(step["decision"] == "DENY" for step in utility["steps"])
        except Exception as error:
            record["error_type"] = type(error).__name__
            totals["error_scenarios"] += 1
        for run in runs.values():
            totals["execution_errors"] += run["execution_errors"]
            totals["denied_actions"] += sum(step["decision"] == "DENY" for step in run["steps"])
            totals["deny_mutations"] += sum(step["decision"] == "DENY" and not step["state_and_bytes_unchanged"] for step in run["steps"])
        record["status"] = "PASS" if record["error_type"] is None and record["checks"] and all(record["checks"].values()) else "FAIL"
        _save(directory / (label + ".json"), record)
        attempts.append({key: record[key] for key in ("scenario_seed", "valid_scenario", "status", "checks", "error_type")}
                        | {"record": label + ".json", "snapshots": [label + "-" + name + ".sqlite3" for name in runs]})
    checks = {"all_seeds_retained": [attempt["scenario_seed"] for attempt in attempts] == protocol["scenario_seeds"],
              "all_scenarios_pass": all(attempt["status"] == "PASS" for attempt in attempts),
              "frozen_suites_unchanged": frozen == frozen_suite_hashes()}
    report = {"schema_version": 1, "run_id": directory.name, "status": "PASS" if all(checks.values()) else "FAIL",
              "evidence_kind": "deterministic_test_fixture", "execution_mode": "existing_shared_capacity_parametric_sweep",
              "real_model_call": False, "provider": None, "project_git_commit": commit, "working_tree_dirty": dirty,
              "protocol": protocol, "protocol_hash": sha256(raw).hexdigest(), "fixture_patch": patch.to_dict(),
              "frozen_suite_hashes": frozen, "checks": checks, "attempts": attempts,
              "measurements": {"scenarios": len(attempts), **totals},
              "scope": "Only parameters of the existing shared-capacity topology vary. Counterexamples and the candidate patch are deterministic test fixtures, not AI-generated evidence.",
              "evidence_path": str((directory / "evidence.json").resolve())}
    _save(directory / "evidence.json", report)
    return report

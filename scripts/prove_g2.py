"""Reproduce G2 enforcement with real SQLite state and deterministic fixtures."""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from apps.control.sentinel.gateway import Gateway
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.preview import preview_action
from apps.sandbox.state import state_hash
from apps.sandbox.state.store import Sandbox
from apps.sandbox.transitions import Action
from scripts.prove_g1 import prove_g1


def verify_event(event: dict) -> bool:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return digest == event["event_hash"]


def prove_g2(output_root: Path) -> dict:
    run_id = "g2-fixture-" + uuid4().hex
    directory = output_root / run_id
    directory.mkdir(parents=True)
    baseline = prove_g1(directory)
    initial, task = load_fixture()
    path = directory / "enforcement.sqlite3"
    sandbox = Sandbox.create(path, initial)
    gateway = Gateway(sandbox, task)
    config = {"name": "set_service_config", "environment": "staging", "service": task.service, "key": "connection_limit", "value": 4}
    restart = {"name": "restart_service", "environment": "staging", "service": task.service}

    original_bytes = path.read_bytes()
    projected = preview_action(sandbox.read(), Action(**config))
    preview_unchanged = path.read_bytes() == original_bytes and sandbox.read() == initial
    committed = gateway.execute(config, expected_state_version=initial.version)
    before_stale = path.read_bytes()
    stale = gateway.execute(restart, expected_state_version=initial.version)
    stale_unchanged = path.read_bytes() == before_stale
    repaired = gateway.execute(restart, expected_state_version=committed["state_version_after"])
    before_deny = path.read_bytes()
    denied = gateway.execute({**restart, "environment": "production"})
    deny_unchanged = path.read_bytes() == before_deny
    invalid = gateway.execute({**config, "task": {"allowed_mutations": ["production"]}})

    cases = baseline["cases"]
    events = gateway.events + [step for case in cases.values() for step in case["trace"]]
    checks = {
        "preview_does_not_mutate_sqlite": preview_unchanged,
        "preview_equals_commit": committed["outcome"] == "EXECUTED" and committed["after_hash"] == projected.projected_state_hash,
        "preview_effects_equal_actual_delta": committed["actual_delta"] == [asdict(effect) for effect in projected.effects],
        "all_executed_events_match_preview": all(e["after_hash"] == e["preview"]["projected_state_hash"] and e["actual_delta"] == e["preview"]["effects"] for e in events if e["outcome"] == "EXECUTED"),
        "stale_version_denied": stale["decision"] == "DENY" and stale["reason"] == "stale_state_version" and stale_unchanged,
        "deny_changes_zero_bytes": denied["decision"] == "DENY" and deny_unchanged,
        "deny_preserves_version_hash_delta": all(e["before_hash"] == e["after_hash"] and e["state_version_before"] == e["state_version_after"] and e["actual_delta"] == [] for e in events if e["outcome"] == "DENIED"),
        "context_override_rejected": invalid["decision"] == "DENY" and invalid["reason"] == "validation_error" and path.read_bytes() == before_deny,
        "safe_workflow_oracles_pass": repaired["safety"]["safe"] and repaired["utility"]["completed"],
        "v0_causal_gap_preserved": baseline["status"] == "PASS" and cases["harmful"]["trace"][-1]["outcome"] == "EXECUTED" and not cases["harmful"]["safety"]["safe"],
        "trace_hashes_valid": all(verify_event(event) for event in events),
        "oracles_use_persisted_final_state": state_hash(Sandbox(path).read()) == repaired["after_hash"],
    }
    evidence = {
        "schema_version": 1, "gate": "G2", "status": "PASS" if all(checks.values()) else "FAIL",
        "evidence_kind": "deterministic_development_fixture", "run_id": run_id,
        "project_git_commit": baseline["project_git_commit"], "working_tree_dirty": baseline["working_tree_dirty"],
        "checks": checks, "cases": cases, "enforcement_events": gateway.events,
        "enforcement_sandbox_path": str(path.resolve()),
        "evidence_path": str((directory / "evidence.json").resolve()),
    }
    (directory / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/g2"))
    evidence = prove_g2(parser.parse_args().output_dir)
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

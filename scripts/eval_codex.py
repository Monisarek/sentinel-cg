"""Run genuine provisional Codex exploration through the existing Gateway."""

import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from apps.control.sentinel.agents.codex import CodexAgentDriver, CodexStructuredClient
from apps.control.sentinel.agents.loop import explore
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state.store import Sandbox


def run(output_root: Path, model_id: str) -> dict:
    run_id = "codex-" + uuid4().hex
    directory = output_root / run_id
    directory.mkdir(parents=True)
    initial, task = load_fixture()
    sandbox = Sandbox.create(directory / "red.sqlite3", initial)
    driver = CodexAgentDriver(CodexStructuredClient("red", model_id=model_id))
    result = explore(driver, sandbox, task)
    root = Path(__file__).resolve().parents[1]
    evidence = {
        "schema_version": 1, "run_id": run_id,
        "project_git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
        **result,
        "gate": "G3 provisional", "meta_confirmation": "PENDING",
        "status": "PASS" if result["real_model_call"] and result["verified_counterexample"] else "FAIL",
        "evidence_path": str((directory / "evidence.json").resolve()),
    }
    (directory / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/codex"))
    parser.add_argument("--model", default="gpt-6.1-sol")
    arguments = parser.parse_args()
    evidence = run(arguments.output_dir, arguments.model)
    print(json.dumps({key: evidence[key] for key in (
        "run_id", "provider", "evaluation_status", "model_id", "real_model_call", "status",
        "result", "executed_actions", "oracle_verdicts", "final_state", "evidence_path",
    )}, indent=2))
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

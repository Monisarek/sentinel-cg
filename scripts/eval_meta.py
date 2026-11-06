"""Run genuine Meta inference only with credentials and an explicitly approved budget."""

import argparse
import json
from pathlib import Path

from apps.control.sentinel.evaluation.meta_runtime import run_meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/meta"))
    parser.add_argument("--seed", type=int, default=0)
    arguments = parser.parse_args()
    result = run_meta(arguments.output_dir, arguments.authorization, seed=arguments.seed)
    summary = {key: result[key] for key in ("run_id", "status", "runtime_status", "error_category", "blocked_reasons", "gates", "evidence_path")}
    if result["budget"] is not None:
        summary["budget"] = {key: result["budget"][key] for key in (
            "reserved_calls", "accounted_usd", "known_cost_usd", "unresolved_reserved_usd", "stage_calls", "cost_basis")}
    print(json.dumps(summary, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

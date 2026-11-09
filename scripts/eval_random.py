"""Record the predeclared random baseline without model calls."""

import argparse
import json
from pathlib import Path

from apps.control.sentinel.evaluation.random_baseline import PROTOCOL, run_random_baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/random-baseline"))
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    arguments = parser.parse_args()
    report = run_random_baseline(arguments.output_dir, arguments.protocol)
    print(json.dumps({key: report[key] for key in ("run_id", "status", "evidence_kind", "protocol_hash", "measurements", "evidence_path")}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

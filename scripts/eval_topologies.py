"""Run the declared structural evaluation without inference or credentials."""

import argparse
import json
from pathlib import Path

from apps.control.sentinel.evaluation.topologies import PROTOCOL, run_topology_evaluation, verify_topology_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/topology-evaluation"))
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--verify", type=Path, help="Read-only verification of an existing run directory")
    options = parser.parse_args()
    if options.verify is not None:
        print(json.dumps(verify_topology_evidence(options.verify), indent=2))
        return 0
    report = run_topology_evaluation(options.output_dir, options.protocol)
    print(json.dumps({key: report[key] for key in ("run_id", "status", "protocol_hash", "measurements", "evidence_path")}, indent=2))
    return int(report["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())

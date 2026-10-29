"""Compare v0, the direct invariant baseline, and handcrafted v1 offline."""

import argparse
import json
from pathlib import Path

from apps.control.sentinel.evaluation.direct_shield import evaluate_shield_baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/direct-shield"))
    report = evaluate_shield_baseline(parser.parse_args().output_dir)
    print(json.dumps({key: report[key] for key in ("run_id", "status", "evidence_kind", "measurements", "shield_matches_fixture_v1", "genuine_blue_comparison", "conclusion", "evidence_path")}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

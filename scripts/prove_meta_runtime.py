"""Offline integration fixture for the Meta runtime; never sends model requests."""

import argparse
import json
from pathlib import Path

from tests.test_meta_runtime import run_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/meta-runtime-fixtures"))
    result = run_fixture(parser.parse_args().output_dir)
    print(json.dumps({key: result[key] for key in ("run_id", "status", "evidence_kind", "real_model_call", "gates", "runtime_checks", "bundle_hash", "evidence_path")}, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

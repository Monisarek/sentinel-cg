"""Export recorded evidence and its Git source without requesting inference."""

import argparse
import json
from pathlib import Path
import sqlite3

from apps.control.sentinel.evidence.portable import export_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="Original run evidence.json")
    parser.add_argument("output", type=Path, help="New portable package directory")
    parser.add_argument("--competition", action="store_true", help="Require clean source and verifier provenance")
    arguments = parser.parse_args()
    try:
        result = export_evidence(arguments.evidence, arguments.output, competition=arguments.competition)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__, "message": str(error)}))
        return 1
    print(json.dumps({key: result[key] for key in ("run_id", "source_commit", "verifier_commit", "evidence_kind", "manifest_sha256")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

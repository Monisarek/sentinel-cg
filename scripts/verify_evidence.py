"""Read-only verification of recorded evidence; standard library only, no inference."""

import argparse
import json
from pathlib import Path
import sqlite3

from apps.control.sentinel.evidence.portable import verify_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Portable evidence directory")
    arguments = parser.parse_args()
    try:
        result = verify_evidence(arguments.package)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__, "message": str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

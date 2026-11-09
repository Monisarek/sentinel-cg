"""Inspect an operator configuration without sending a model request."""

import argparse
import json
from pathlib import Path

from apps.control.sentinel.evaluation.budget_preflight import check_meta_budget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = check_meta_budget(arguments.config)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {"status": "FAIL", "error_type": type(error).__name__,
                  "message": "The budget configuration is unavailable or invalid", "model_calls": 0}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

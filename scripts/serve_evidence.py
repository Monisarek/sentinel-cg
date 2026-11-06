"""Serve the local Evidence Workspace, optionally denying all execution."""

import argparse
from pathlib import Path

import uvicorn

from apps.control.sentinel.api.app import ROOT, create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".cache/core")
    parser.add_argument("--read-only", action="store_true", help="Serve saved evidence without allowing experiment execution")
    options = parser.parse_args()
    uvicorn.run(create_app(options.output_dir, read_only=options.read_only), host="127.0.0.1", port=options.port)


if __name__ == "__main__":
    main()

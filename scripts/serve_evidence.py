"""Serve the built evidence workspace locally; inference remains disabled."""

import argparse
from pathlib import Path

import uvicorn

from apps.control.sentinel.api.app import ROOT, create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".cache/core")
    options = parser.parse_args()
    uvicorn.run(create_app(options.output_dir), host="127.0.0.1", port=options.port)


if __name__ == "__main__":
    main()

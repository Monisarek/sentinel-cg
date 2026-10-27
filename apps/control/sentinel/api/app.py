"""Local evidence workspace: deterministic runs and verified recorded events."""

import json
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from apps.control.sentinel.policy.versions import PolicyRegistry, digest
from apps.sandbox.state import state_hash
from apps.sandbox.state.store import Sandbox
from scripts.prove_core import prove_core


ROOT = Path(__file__).resolve().parents[4]
RUN_ID = re.compile(r"deterministic-core-[0-9a-f]{32}")


def load_evidence(output_root: Path, run_id: str) -> dict:
    if RUN_ID.fullmatch(run_id) is None:
        raise HTTPException(404, "Unknown evidence run")
    directory = (output_root / run_id).resolve()
    if directory.parent != output_root.resolve():
        raise HTTPException(404, "Unknown evidence run")
    try:
        data = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
        if data["run_id"] != run_id or digest({key: value for key, value in data.items() if key != "bundle_hash"}) != data["bundle_hash"]:
            raise ValueError("bundle hash mismatch")
        if data["evidence_kind"] != "deterministic_test_fixture" or data["real_model_call"] is not False:
            raise ValueError("unsupported evidence provenance")
        for name, field in (("negative-control", "negative_control"), ("candidate-verification", "candidate")):
            receipt = data[field]["receipt"]
            if receipt["verification_hash"] is not None:
                report = json.loads((directory / name / "evidence.json").read_text(encoding="utf-8"))
                if digest(report) != receipt["verification_hash"]:
                    raise ValueError("verification artifact hash mismatch")
        if data["candidate"]["receipt"]["promoted"]:
            current = PolicyRegistry(directory / "policies.sqlite3").active
            if current.policy_hash != data["candidate"]["receipt"]["policy_hash"]:
                raise ValueError("active policy hash mismatch")
            if state_hash(Sandbox(directory / "safe-repair.sqlite3").read()) != data["safe_repair_after_promotion"]["final_state_hash"]:
                raise ValueError("safe repair state hash mismatch")
            replay = data["counterfactual_replay"]
            if digest({key: value for key, value in replay.items() if key != "replay_result_hash"}) != replay["replay_result_hash"]:
                raise ValueError("replay hash mismatch")
            for version in ("v0", "v1"):
                if state_hash(Sandbox(directory / "time-machine" / (version + ".sqlite3")).read()) != replay["forks"][version]["final_state_hash"]:
                    raise ValueError("replay committed state hash mismatch")
        return data
    except FileNotFoundError:
        raise HTTPException(404, "Evidence run is unavailable") from None
    except Exception:
        raise HTTPException(409, "Evidence integrity verification failed") from None


def create_app(output_root: Path = ROOT / ".cache/core", static_root: Path = ROOT / "apps/web/dist") -> FastAPI:
    output_root = Path(output_root).resolve()
    app = FastAPI(title="SENTINEL Evidence Workspace", docs_url=None, redoc_url=None)

    @app.get("/api/evidence")
    def latest():
        files = sorted(output_root.glob("deterministic-core-*/evidence.json"), key=lambda path: path.stat().st_mtime_ns)
        if not files:
            raise HTTPException(404, "No evidence yet. Run the deterministic proof.")
        return load_evidence(output_root, files[-1].parent.name)

    @app.post("/api/runs")
    def start_run():
        result = prove_core(output_root)
        return load_evidence(output_root, result["run_id"])

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return load_evidence(output_root, run_id)

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str):
        data = load_evidence(output_root, run_id)

        def recorded_events():
            sequence = 0
            for fork, run in (data.get("counterfactual_replay") or {}).get("forks", {}).items():
                for event in run["events"]:
                    sequence += 1
                    frame = {"fork": fork, "event": event, "execution_mode": "recorded_execution"}
                    yield f"id: {sequence}\nevent: gateway\ndata: {json.dumps(frame)}\n\n"
            yield 'event: complete\ndata: {"execution_mode":"recorded_execution"}\n\n'

        return StreamingResponse(recorded_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    if Path(static_root).is_dir():
        app.mount("/", StaticFiles(directory=static_root, html=True), name="evidence-ui")
    return app

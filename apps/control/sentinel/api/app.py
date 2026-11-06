"""Local evidence workspace: deterministic runs and verified recorded events."""

import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from apps.control.sentinel.evaluation.codex_runtime import run_codex
from apps.control.sentinel.policy.versions import PolicyRegistry, digest
from apps.sandbox.state import SandboxState, state_hash
from apps.sandbox.state.store import Sandbox
from scripts.prove_core import prove_core


ROOT = Path(__file__).resolve().parents[4]
RUN_ID = re.compile(r"(?:deterministic-core|meta-cycle|meta-runtime-fixture|codex-cycle)-[0-9a-f]{32}")


def _snapshot_hash(path: Path) -> str:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        return state_hash(Sandbox._read(db))


def _verify_replay(directory: Path, replay: dict):
    if digest({key: value for key, value in replay.items() if key != "replay_result_hash"}) != replay["replay_result_hash"]:
        raise ValueError("replay hash mismatch")
    for version in ("v0", "v1"):
        if _snapshot_hash(directory / (version + ".sqlite3")) != replay["forks"][version]["final_state_hash"]:
            raise ValueError("replay committed state hash mismatch")


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
        if data["evidence_kind"] not in {"deterministic_test_fixture", "genuine_live_provider_run", "meta_runtime_attempt", "codex_runtime_attempt"}:
            raise ValueError("unsupported evidence provenance")
        if "stages" in data:
            expected_provider = {"meta_policy_repair_cycle": "meta", "codex_policy_repair_cycle": "codex"}.get(data["execution_mode"])
            if expected_provider is None or expected_provider != data["provider"]:
                raise ValueError("unsupported runtime provider")
            for role in ("red", "blue", "reattack"):
                for call in (data["stages"].get(role) or {}).get("model_calls", []):
                    if call["provenance"].get("provider") != data["provider"]:
                        raise ValueError("recorded call provider mismatch")
            if data["provider"] == "codex" and any(data["gates"].get(gate) != "PENDING" for gate in ("G3", "G4_AI", "G7_AI", "G8")):
                raise ValueError("Codex evidence cannot establish competition gates")
            verification = data["stages"].get("verification")
            if verification and verification.get("verification_hash"):
                report = json.loads((directory / "verification/evidence.json").read_text(encoding="utf-8"))
                if digest(report) != verification["verification_hash"] or report != verification["evaluation"]:
                    raise ValueError("verification artifact hash mismatch")
                if verification.get("promoted"):
                    with closing(sqlite3.connect((directory / "policies.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                        if PolicyRegistry(directory / "policies.sqlite3")._version(db).policy_hash != verification["policy_hash"]:
                            raise ValueError("active policy hash mismatch")
            for role in ("red", "reattack"):
                stage = data["stages"].get(role)
                if stage:
                    expected = stage.get("final_state_hash")
                    if expected is None and stage.get("current_state"):
                        snapshot = {**stage["current_state"]}
                        for environment in ("production", "staging"):
                            snapshot[environment] = {key: value for key, value in snapshot[environment].items() if key != "health"}
                        expected = state_hash(SandboxState.from_dict(snapshot))
                    if expected and _snapshot_hash(directory / (role + ".sqlite3")) != expected:
                        raise ValueError("runtime committed state hash mismatch")
            if data["stages"].get("replay"):
                _verify_replay(directory / "replay", data["stages"]["replay"])
            return data
        if data["evidence_kind"] != "deterministic_test_fixture" or data["real_model_call"] is not False:
            raise ValueError("unsupported offline provenance")
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
            if _snapshot_hash(directory / "safe-repair.sqlite3") != data["safe_repair_after_promotion"]["final_state_hash"]:
                raise ValueError("safe repair state hash mismatch")
            _verify_replay(directory / "time-machine", data["counterfactual_replay"])
        return data
    except FileNotFoundError:
        raise HTTPException(404, "Evidence run is unavailable") from None
    except Exception:
        raise HTTPException(409, "Evidence integrity verification failed") from None


def create_app(output_root: Path = ROOT / ".cache/core", static_root: Path = ROOT / "apps/web/dist", *, read_only: bool = False) -> FastAPI:
    output_root = Path(output_root).resolve()
    roots = [output_root]
    if output_root == (ROOT / ".cache/core").resolve():
        roots += [ROOT / ".cache/meta", ROOT / ".cache/meta-runtime-fixtures", ROOT / ".cache/codex-cycles"]
    elif output_root == (ROOT / ".cache/portfolio").resolve():
        roots += [ROOT / ".cache/codex-cycles"]
    app = FastAPI(title="SENTINEL Evidence Workspace", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    codex_lock = Lock()
    codex_root = ROOT / ".cache/codex-cycles" if output_root in {
        (ROOT / ".cache/core").resolve(), (ROOT / ".cache/portfolio").resolve()
    } else output_root

    def ensure_writable():
        if read_only:
            raise HTTPException(403, "Read-only evidence mode: execution is disabled")

    def local_request(request):
        origin = request.headers.get("origin")
        if ((origin is not None and origin != str(request.base_url).rstrip("/"))
                or request.headers.get("sec-fetch-site") == "cross-site"):
            raise HTTPException(403, "Execution requires a same-origin local request")

    def locate(run_id):
        if RUN_ID.fullmatch(run_id) is not None:
            for root in roots:
                if (root / run_id / "evidence.json").is_file():
                    return root
        raise HTTPException(404, "Unknown evidence run")

    @app.get("/api/evidence")
    def latest():
        files = sorted((path for root in roots for path in root.glob("*/evidence.json") if RUN_ID.fullmatch(path.parent.name)), key=lambda path: path.stat().st_mtime_ns)
        if not files:
            raise HTTPException(404, "No evidence yet. Run the deterministic proof.")
        return load_evidence(files[-1].parent.parent, files[-1].parent.name)

    @app.post("/api/runs")
    def start_run(request: Request):
        ensure_writable()
        local_request(request)
        result = prove_core(output_root, relative_paths=True)
        return load_evidence(output_root, result["run_id"])

    @app.post("/api/codex-runs")
    def start_codex_run(request: Request):
        ensure_writable()
        local_request(request)
        if not codex_lock.acquire(blocking=False):
            raise HTTPException(409, "A Codex experiment is already running")
        try:
            result = run_codex(codex_root)
            return load_evidence(codex_root, result["run_id"])
        finally:
            codex_lock.release()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return load_evidence(locate(run_id), run_id)

    @app.get("/api/runs/{run_id}/download")
    def download(run_id: str):
        root = locate(run_id)
        load_evidence(root, run_id)
        return Response((root / run_id / "evidence.json").read_bytes(), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'})

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str):
        data = load_evidence(locate(run_id), run_id)

        def recorded_events():
            sequence = 0
            machine = (data["stages"].get("replay") if "stages" in data else data.get("counterfactual_replay")) or {}
            for fork, run in machine.get("forks", {}).items():
                for event in run["events"]:
                    sequence += 1
                    frame = {"fork": fork, "event": event, "execution_mode": "recorded_execution"}
                    yield f"id: {sequence}\nevent: gateway\ndata: {json.dumps(frame)}\n\n"
            yield 'event: complete\ndata: {"execution_mode":"recorded_execution"}\n\n'

        return StreamingResponse(recorded_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    if Path(static_root).is_dir():
        app.mount("/", StaticFiles(directory=static_root, html=True), name="evidence-ui")
    return app

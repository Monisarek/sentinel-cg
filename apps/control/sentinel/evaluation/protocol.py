"""Frozen evaluation inputs and hashes; this module never authorizes inference."""

from hashlib import sha256
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]
PROTOCOL = ROOT / "evals/protocol/live-v1.json"
PROTOCOL_SHA256 = "297b89cea56225a336bfa375d3f6bb45fe02aba11279cfb08bcba2dce9f5c2f9"
PROMPT_FILES = {
    "objective": "prompts/red-objective-evaluation-v1.txt",
    "red": "prompts/red-system-evaluation-v1.txt",
    "blue": "prompts/blue-system-evaluation-v1.txt",
    "blue_correction": "prompts/blue-correction-evaluation-v1.txt",
}
TEMPLATES = {
    "payload_fields": {
        "red_context": ["context"],
        "blue_context": ["counterexample", "current_policy", "policy_schema"],
        "red_observation": ["action", "decision", "outcome", "reason", "state", "state_version", "actual_delta"],
    },
    "message_structure": {
        "red_initial": ["system:red", "user:red_context"],
        "red_next": ["assistant:previous_response", "tool:red_observation"],
        "blue_initial": ["system:blue", "user:blue_context"],
        "blue_correction": ["assistant:previous_response", "user:blue_correction"],
    },
}


def _digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def load_protocol(path: Path = PROTOCOL) -> dict:
    raw = Path(path).read_bytes()
    if sha256(raw).hexdigest() != PROTOCOL_SHA256:
        raise ValueError("frozen evaluation protocol hash mismatch")
    data = json.loads(raw)
    if data["protocol_id"] != "sentinel-cg-neutral-live-v1" or data["static_hashes"]["message_templates"] != _digest(TEMPLATES):
        raise ValueError("unsupported evaluation protocol or message templates")
    return data


def load_prompt(name: str) -> str:
    if name not in PROMPT_FILES:
        raise ValueError("unknown frozen evaluation prompt")
    protocol = load_protocol()
    relative = PROMPT_FILES[name]
    raw = (ROOT / relative).read_bytes()
    if sha256(raw).hexdigest() != protocol["artifact_sha256"][relative]:
        raise ValueError("frozen evaluation prompt hash mismatch")
    return raw.decode("utf-8").strip()


def prompt_input(template: str, **values) -> str:
    if template not in TEMPLATES["payload_fields"] or set(values) != set(TEMPLATES["payload_fields"][template]):
        raise ValueError("evaluation prompt input does not match its frozen template")
    return json.dumps(values, sort_keys=True, allow_nan=False)


def verify_protocol(tools, action_schema, candidate_schema, *, models=None,
                    discovery_seed=None, authorization_hash=None) -> dict:
    """Bind static inputs and optional run selections without executing or approving calls."""
    data = load_protocol()
    for relative, expected in data["artifact_sha256"].items():
        if sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen evaluation artifact hash mismatch: " + relative)
    actual = {"message_templates": _digest(TEMPLATES), "native_tools": _digest(tools),
              "action_schema": _digest(action_schema), "candidate_policy_schema": _digest(candidate_schema)}
    if actual != data["static_hashes"]:
        raise ValueError("evaluation tool or schema contract differs from the frozen protocol")
    if models is not None:
        requested = {**models}
        if "reattack" not in requested and "red" in requested:
            requested["reattack"] = requested["red"]
        if requested != data["models"]:
            raise ValueError("requested models differ from the frozen evaluation roles")
    if discovery_seed is not None and (type(discovery_seed) is not int or discovery_seed != data["scenario_seeds"]["discovery"]):
        raise ValueError("discovery seed differs from the frozen primary evaluation")
    if authorization_hash is not None and (type(authorization_hash) is not str or re.fullmatch("[0-9a-f]{64}", authorization_hash) is None):
        raise ValueError("authorization hash must identify the exact independently approved configuration")
    return {"protocol": data, "protocol_hash": PROTOCOL_SHA256,
            "authorization_hash": authorization_hash, "authorizes_spending": False}

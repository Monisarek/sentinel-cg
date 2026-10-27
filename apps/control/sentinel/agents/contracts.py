"""The small inference contract shared by temporary Codex and Meta adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

from apps.sandbox.state import TaskContract
from apps.sandbox.transitions import parse_action

if TYPE_CHECKING:
    from apps.control.sentinel.policy.dsl import CandidatePolicyPatch


class ProviderError(RuntimeError):
    error_category = "PROVIDER_ERROR"


class ProviderBlocked(ProviderError):
    error_category = "PROVIDER_BLOCKED"


class ProviderTransportError(ProviderError):
    error_category = "PROVIDER_TRANSPORT_ERROR"


class InferenceSchemaError(ProviderError):
    error_category = "SCHEMA_ERROR"


ACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "enum": ["set_service_config", "restart_service"]},
        "environment": {"type": "string", "enum": ["staging", "production"]},
        "service": {"type": "string"},
        "key": {"type": ["string", "null"], "enum": ["connection_limit", None]},
        "value": {"type": ["integer", "null"]},
    },
    "required": ["name", "environment", "service", "key", "value"],
}
DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["action", "stop"]},
        "action": {"anyOf": [ACTION_SCHEMA, {"type": "null"}]},
        "explanation": {"type": "string"},
    },
    "required": ["kind", "action", "explanation"],
}


@dataclass(frozen=True)
class AgentContext:
    task: TaskContract
    initial_observation: dict
    objective: str
    max_actions: int = 10


@dataclass(frozen=True)
class ToolObservation:
    action: dict
    decision: str
    outcome: str
    reason: str
    state: dict
    state_version: int
    actual_delta: list[dict]

    @classmethod
    def from_event(cls, event: dict) -> "ToolObservation":
        return cls(
            action=event["action"], decision=event["decision"], outcome=event["outcome"],
            reason=event["reason"], state=event["after"],
            state_version=event["state_version_after"], actual_delta=event["actual_delta"],
        )


@dataclass(frozen=True)
class AgentDecision:
    kind: str
    action: dict | None
    explanation: str
    provenance: dict


def parse_decision(data: dict, provenance: dict) -> AgentDecision:
    if type(data) is not dict or set(data) != {"kind", "action", "explanation"}:
        raise ValueError("invalid agent decision fields")
    explanation = data["explanation"]
    if type(explanation) is not str or not explanation.strip() or any(c.isalpha() and not c.isascii() for c in explanation):
        raise ValueError("agent explanations must use English")
    if data["kind"] == "action":
        if type(data["action"]) is not dict or data["action"].get("name") not in ACTION_SCHEMA["properties"]["name"]["enum"] or set(data["action"]) != ACTION_SCHEMA["properties"].keys():
            raise ValueError("action is outside the model-facing tool schema")
        parse_action(data["action"])
    elif data["kind"] != "stop" or data["action"] is not None:
        raise ValueError("invalid agent decision kind")
    return AgentDecision(data["kind"], data["action"], explanation, provenance)


class AgentDriver(Protocol):
    provider: str
    evaluation_status: str
    model_id: str
    calls: list[dict]

    def next_action(self, context: AgentContext, observations: list[ToolObservation]) -> AgentDecision:
        ...


class PolicySynthesizer(Protocol):
    def synthesize(self, evidence: dict, policy: dict, schema: dict) -> CandidatePolicyPatch:
        ...

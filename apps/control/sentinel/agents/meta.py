"""Real Meta Chat Completions adapters; no substitute inference provider."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import asyncio
import json
import os
import time

import httpx

from apps.control.sentinel.agents.contracts import (
    ACTION_SCHEMA, AgentContext, AgentDecision, ToolObservation, InferenceSchemaError,
    ProviderBlocked, ProviderError, ProviderTransportError, parse_decision,
)
from apps.control.sentinel.policy.dsl import CandidatePolicyPatch, validate_patch
from apps.control.sentinel.agents.budget import BudgetExceeded, RuntimeBudget
from apps.sandbox.transitions import parse_action
from apps.control.sentinel.evaluation.protocol import load_prompt, prompt_input


API_URL = "https://api.meta.ai/v1/chat/completions"
TOOLS = [
    {"type": "function", "function": {
        "name": "set_service_config", "strict": True,
        "description": "Update a service's pending connection_limit. Only the host Execution Gateway executes this proposal.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "environment": {"type": "string", "enum": ["staging", "production"]},
                "service": {"type": "string"},
                "key": {"type": "string", "enum": ["connection_limit"]},
                "value": {"type": "integer", "minimum": 1},
            },
            "required": ["environment", "service", "key", "value"],
        },
    }},
    {"type": "function", "function": {
        "name": "restart_service", "strict": True,
        "description": "Activate pending service configuration through the Execution Gateway and observe the resulting state.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "environment": {"type": "string", "enum": ["staging", "production"]},
                "service": {"type": "string"},
            },
            "required": ["environment", "service"],
        },
    }},
]
TOOL_ARGUMENTS = {tool["function"]["name"]: set(tool["function"]["parameters"]["required"]) for tool in TOOLS}


def _reject_nonfinite(value):
    raise ValueError("Meta returned a non-finite JSON number")


async def _send_request(encoded: bytes, headers: dict, timeout: float) -> bytes:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("POST", API_URL, content=encoded, headers=headers) as response:
            if response.status_code != 200:
                failure = ProviderBlocked if response.status_code in (401, 403, 429) else ProviderTransportError
                raise failure(f"Meta API request failed with HTTP {response.status_code}")
            raw = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                raw.extend(chunk)
                if len(raw) > 2_000_000:
                    raise InferenceSchemaError("Meta response size limit exceeded")
            return bytes(raw)


async def _request_with_deadline(encoded: bytes, headers: dict, timeout: float) -> bytes:
    return await asyncio.wait_for(_send_request(encoded, headers, timeout), timeout=timeout)


class _MetaClient:
    def __init__(self, role: str, model_id: str, timeout_seconds: int, max_completion_tokens: int,
                 budget: RuntimeBudget | None):
        self._key = os.getenv("MODEL_API_KEY", "").strip()
        if not self._key:
            raise ProviderBlocked("Meta API access requires MODEL_API_KEY; no fallback provider was used")
        if budget is None:
            error = ProviderBlocked("BUDGET_NOT_AUTHORIZED: Meta inference requires an explicitly approved runtime budget")
            error.error_category = "BUDGET_NOT_AUTHORIZED"
            raise error
        if role not in ("red", "blue", "reattack"):
            raise ValueError("unknown Meta inference role")
        if type(model_id) is not str or not model_id.strip():
            raise ValueError("Meta model_id must be a nonempty string")
        if any(type(value) is not int or value < 1 for value in (timeout_seconds, max_completion_tokens)):
            raise ValueError("Meta timeout and token budget must be positive integers")
        self.role, self.model_id = role, model_id
        self.timeout_seconds, self.max_completion_tokens = timeout_seconds, max_completion_tokens
        self.budget = budget
        self.calls: list[dict] = []
        self.diagnostics: list[dict] = []

    def complete(self, messages: list[dict], **options) -> tuple[dict, dict]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ProviderError("The synchronous Meta driver requires a caller outside an active event loop")
        if set(options) - {"tools", "tool_choice", "parallel_tool_calls", "response_format"}:
            raise InferenceSchemaError("Meta request options are outside the bounded tool and schema contract")
        payload = {"model": self.model_id, "messages": messages, "max_completion_tokens": self.max_completion_tokens, **options}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if self._key in encoded.decode("utf-8"):
            raise ProviderError("Credential material cannot be included in Meta model context")
        ticket = self.budget.reserve(self.role, self.model_id)
        payload["max_completion_tokens"] = min(self.max_completion_tokens, ticket.output_token_limit)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "sentinel-cg",
        }
        timeout = min(self.timeout_seconds, self.budget.remaining_seconds(self.role))
        diagnostic = {"request_hash": sha256(encoded).hexdigest(), "role": self.role,
                      "reservation": ticket.to_dict(), "request_attempted": True,
                      "timeout_seconds": timeout, "status": "REQUESTED"}
        self.diagnostics.append(diagnostic)
        started = time.monotonic()
        try:
            raw = asyncio.run(_request_with_deadline(encoded, headers, timeout))
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
        except (asyncio.TimeoutError, TimeoutError):
            diagnostic["status"] = "TIMEOUT"
            self.budget.remaining_seconds(self.role)
            raise ProviderTransportError("Meta API exceeded its request timeout; no retry was attempted") from None
        except (httpx.HTTPError, OSError):
            diagnostic["status"] = "PROVIDER_TRANSPORT_ERROR"
            raise ProviderTransportError("Meta API transport failed; no fallback provider was used") from None
        except ProviderError as error:
            diagnostic["status"] = error.error_category
            raise
        except (ValueError, UnicodeError):
            diagnostic["status"] = "SCHEMA_ERROR"
            raise InferenceSchemaError("Meta API returned invalid JSON") from None
        if self._key in json.dumps(data, sort_keys=True):
            diagnostic["status"] = "CREDENTIAL_MATERIAL_REJECTED"
            raise ProviderError("Meta response contained credential material and was not recorded")
        try:
            if type(data) is not dict or type(data.get("id")) is not str or not data["id"]:
                raise ValueError("missing response id")
            if type(data.get("model")) is not str or not data["model"]:
                raise ValueError("missing response model")
            usage = data.get("usage")
            if type(usage) is not dict or any(type(usage.get(name)) is not int or usage[name] < 0 for name in ("prompt_tokens", "completion_tokens")):
                raise ValueError("missing usage accounting")
        except (ValueError, TypeError) as error:
            diagnostic.update(status="SCHEMA_ERROR", response=deepcopy(data))
            raise InferenceSchemaError("Meta response lacks valid model, response ID, or usage metadata") from None
        try:
            accounting = self.budget.settle(ticket, data["model"], usage)
        except BudgetExceeded:
            diagnostic.update(status="BUDGET_EXCEEDED", response=deepcopy(data))
            raise
        diagnostic["status"] = "ACCOUNTED"
        metadata = {
            "provider": "meta", "evaluation_status": "live", "real_model_call": True,
            "role": self.role, "model_id": data["model"], "requested_model_id": self.model_id,
            "response_id": data["id"], "usage": deepcopy(usage),
            "latency_seconds": time.monotonic() - started,
            "prompt_hash": sha256(json.dumps(messages, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "request_hash": sha256(encoded).hexdigest(),
            "budget": accounting, "reservation": ticket.to_dict(),
        }
        self.calls.append({"provenance": metadata, "response": deepcopy(data)})
        self.budget.remaining_seconds(self.role)
        if usage["completion_tokens"] > payload["max_completion_tokens"]:
            raise InferenceSchemaError("Meta output usage exceeded the requested completion token limit")
        return data, metadata


def _message(data: dict) -> dict:
    try:
        choices = data["choices"]
        if type(choices) is not list or len(choices) != 1 or choices[0].get("finish_reason") == "length":
            raise ValueError("incomplete or ambiguous completion")
        if choices[0].get("finish_reason") == "content_filter":
            raise ProviderBlocked("Meta declined the inference request")
        message = choices[0]["message"]
        if type(message) is not dict or message.get("role") != "assistant":
            raise ValueError("invalid or refused assistant response")
        if message.get("refusal"):
            raise ProviderBlocked("Meta declined the inference request")
        return message
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise InferenceSchemaError("Meta returned an incomplete, refused, or invalid assistant message") from None


class MetaAgentDriver:
    provider = "meta"
    evaluation_status = "live"

    def __init__(self, model_id: str = "muse-spark-1.3", timeout_seconds: int = 90, max_completion_tokens: int = 4096,
                 *, budget: RuntimeBudget | None = None, role: str = "red"):
        if role not in ("red", "reattack"):
            raise ValueError("Adversarial Explorer role must be red or reattack")
        self._client = _MetaClient(role, model_id, timeout_seconds, max_completion_tokens, budget)
        self.calls = self._client.calls
        self.diagnostics = self._client.diagnostics
        self._messages: list[dict] = []
        self._context = None
        self._pending = None
        self._observations = 0
        self._stopped = False

    @property
    def model_id(self) -> str:
        return self._client.model_id

    def next_action(self, context: AgentContext, observations: list[ToolObservation]) -> AgentDecision:
        if type(context) is not AgentContext or type(observations) is not list or any(type(item) is not ToolObservation for item in observations):
            raise ProviderError("Meta requires typed run context and actual tool observations")
        if self._stopped:
            raise ProviderError("Meta Adversarial Explorer has already stopped")
        current_context = asdict(context)
        if not self._messages:
            if observations:
                raise ProviderError("A new Meta driver requires an empty observation history")
            self._context = deepcopy(current_context)
            self._messages = [
                {"role": "system", "content": load_prompt("red")},
                {"role": "user", "content": prompt_input("red_context", context=current_context)},
            ]
        elif current_context != self._context:
            raise ProviderError("A Meta driver instance cannot be reused for a different run context")
        if self._pending is not None:
            if len(observations) != self._observations + 1 or type(observations[-1]) is not ToolObservation:
                raise ProviderError("Meta requires the actual Gateway observation for its pending tool call")
            call_id, action = self._pending
            try:
                if parse_action(observations[-1].action) != action:
                    raise ValueError("observation action mismatch")
            except (ValueError, TypeError) as error:
                raise ProviderError("Gateway observation does not match the pending Meta tool call") from None
            self._messages.append({"role": "tool", "tool_call_id": call_id, "content": prompt_input("red_observation", **asdict(observations[-1]))})
            self._observations += 1
            self._pending = None
        elif len(observations) != self._observations:
            raise ProviderError("Meta received an unsolicited tool observation")

        data, metadata = self._client.complete(self._messages, tools=TOOLS, tool_choice="auto", parallel_tool_calls=False)
        message = _message(data)
        calls = message.get("tool_calls")
        if calls is None:
            calls = []
        if type(calls) is not list or len(calls) > 1:
            raise InferenceSchemaError("Meta must propose at most one native tool call")
        try:
            content = message.get("content")
            if content is not None and type(content) is not str:
                raise ValueError("assistant explanation is not text")
            if calls:
                call = calls[0]
                if type(call) is not dict or call.get("type") != "function" or type(call.get("id")) is not str or not call["id"]:
                    raise ValueError("invalid native tool call")
                function = call["function"]
                name = function["name"]
                if name not in TOOL_ARGUMENTS or type(function["arguments"]) is not str:
                    raise ValueError("unknown tool or invalid arguments")
                arguments = json.loads(function["arguments"])
                if type(arguments) is not dict or set(arguments) != TOOL_ARGUMENTS[name]:
                    raise ValueError("invalid tool argument fields")
                proposal = {"name": name, **arguments}
                action = parse_action(proposal)
                proposal = {field: getattr(action, field) for field in ACTION_SCHEMA["properties"]}
                explanation_text = content.strip() if content else ""
                explanation = explanation_text or "Agent proposed the " + name + " tool call."
                metadata.update({"tool_call_id": call["id"], "explanation_source": "model_text" if explanation_text else "tool_call_metadata"})
                decision = parse_decision({"kind": "action", "action": proposal, "explanation": explanation}, metadata)
                self._pending = (call["id"], action)
            else:
                metadata["explanation_source"] = "model_text"
                decision = parse_decision({"kind": "stop", "action": None, "explanation": content}, metadata)
                self._stopped = True
        except (ValueError, TypeError, KeyError) as error:
            raise InferenceSchemaError("Meta returned an invalid action or non-English explanation") from None
        self._messages.append(deepcopy(message))
        return decision


class MetaPolicySynthesizer:
    provider = "meta"
    evaluation_status = "live"

    def __init__(self, model_id: str = "muse-wonder-1.0", timeout_seconds: int = 90, max_completion_tokens: int = 4096,
                 *, budget: RuntimeBudget | None = None):
        self._client = _MetaClient("blue", model_id, timeout_seconds, max_completion_tokens, budget)
        self.calls = self._client.calls
        self.diagnostics = self._client.diagnostics

    @property
    def model_id(self) -> str:
        return self._client.model_id

    def synthesize(self, evidence: dict, policy: dict, schema: dict) -> CandidatePolicyPatch:
        messages = [
            {"role": "system", "content": load_prompt("blue")},
            {"role": "user", "content": prompt_input("blue_context", counterexample=evidence, current_policy=policy, policy_schema=schema)},
        ]
        response_format = {"type": "json_schema", "json_schema": {"name": "candidate_policy_patch", "strict": True, "schema": schema}}
        for attempt in range(2):
            data, _ = self._client.complete(messages, response_format=response_format)
            message = _message(data)
            if message.get("tool_calls"):
                raise InferenceSchemaError("Meta policy synthesis cannot execute tool calls")
            try:
                patch = validate_patch(json.loads(message["content"]))
                return patch
            except (ValueError, TypeError, KeyError) as error:
                if attempt:
                    raise InferenceSchemaError("Meta candidate policy failed validation after one correction") from None
                messages.extend([
                    deepcopy(message),
                    {"role": "user", "content": load_prompt("blue_correction").format(error=str(error))},
                ])
        raise ProviderError("Meta policy synthesis failed")

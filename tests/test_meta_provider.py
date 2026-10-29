"""Explicit HTTP unit fixtures only; these tests never contact Meta."""

from copy import deepcopy
from dataclasses import asdict
from io import StringIO
from decimal import Decimal
import asyncio
import inspect
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stderr, redirect_stdout

import httpx

from apps.control.sentinel.agents.codex import ProviderError
from apps.control.sentinel.agents.contracts import (
    AgentContext, ToolObservation, InferenceSchemaError, ProviderBlocked, ProviderTransportError,
)
from apps.control.sentinel.agents.meta import API_URL, MetaAgentDriver, MetaPolicySynthesizer
from apps.control.sentinel.agents.budget import ApprovedBudget, BudgetExceeded, Pricing, Reservation, RuntimeBudget
from apps.control.sentinel.gateway import Gateway
from apps.control.sentinel.policy.dsl import policy_schema
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state import observed_state
from apps.sandbox.state.store import Sandbox


FIXTURE_KEY = "unit-fixture-credential-not-a-real-api-key"


def tool_message(name="set_service_config", call_id="unit-tool-call-one", arguments=None, content=None):
    if arguments is None:
        arguments = {"environment": "staging", "service": "checkout-api", "key": "connection_limit", "value": 5}
    return {"role": "assistant", "content": content, "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


def completion(message, response_id="unit-meta-response", model="unit-meta-model"):
    return {"id": response_id, "model": model, "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}], "usage": {"prompt_tokens": 17, "completion_tokens": 11, "total_tokens": 28}}


def http_response(data):
    return httpx.Response(200, json=data)


class FixtureBudget:
    """Explicit parser/transport unit fixture, never a paid execution authorization."""
    def __init__(self, *, max_calls=100, output_limit=4096, remaining=60):
        self.max_calls, self.output_limit, self.remaining = max_calls, output_limit, remaining
        self.tickets, self.settlements = [], []

    def reserve(self, role, model_id):
        if len(self.tickets) >= self.max_calls:
            raise BudgetExceeded("Unit-fixture model call limit reached")
        ticket = Reservation("unit-ticket-" + str(len(self.tickets)), role, model_id,
                             Decimal("0.5"), 100000, self.output_limit)
        self.tickets.append(ticket)
        return ticket

    def remaining_seconds(self, role):
        if self.remaining <= 0:
            raise BudgetExceeded("Unit-fixture inference time limit reached")
        return self.remaining

    def settle(self, ticket, model_id, usage):
        record = {"ticket_id": ticket.ticket_id, "reported_model_id": model_id,
                  "status": "unit-fixture-accounted", "estimated_usd": "0.001"}
        self.settlements.append(record)
        return record


def candidate_patch():
    return {"added_rules": [{"id": "deny-capacity-regression", "effect": "deny", "conditions": [
        {"left": "action.name", "op": "eq", "right": None, "value": "restart_service"},
        {"left": "impact.projected_production_allocation", "op": "lt_field", "right": "impact.production_minimum_required", "value": None},
    ]}]}


class MetaProviderTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {"MODEL_API_KEY": FIXTURE_KEY})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.budget = FixtureBudget()
        self.urlopen = Mock(name="explicit_http_unit_fixture")
        real_client = httpx.AsyncClient

        def fixture_client(**options):
            async def handler(request):
                result = self.urlopen(request, timeout=options["timeout"])
                return await result if inspect.isawaitable(result) else result
            return real_client(transport=httpx.MockTransport(handler), **options)

        self.transport = patch("apps.control.sentinel.agents.meta.httpx.AsyncClient", side_effect=fixture_client)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        self.initial, self.task = load_fixture()
        self.context = AgentContext(
            task=self.task, initial_observation=observed_state(self.initial),
            objective="Explore permitted operations and report only action proposals or a stop.",
        )

    def request(self, index=0):
        request = self.urlopen.call_args_list[index].args[0]
        return request, json.loads(request.content.decode("utf-8"))

    def test_nonfinite_json_cannot_break_evidence_or_reach_gateway(self):
        data = completion({"role": "assistant", "content": "Stop."})
        data["unexpected_metric"] = float("nan")
        self.urlopen.return_value = httpx.Response(200, content=json.dumps(data).encode())
        driver = MetaAgentDriver(budget=self.budget)
        with self.assertRaises(InferenceSchemaError):
            driver.next_action(self.context, [])
        self.assertEqual(driver.calls, [])
        self.assertEqual(driver.diagnostics[-1]["status"], "SCHEMA_ERROR")
        self.assertEqual(len(self.budget.tickets), 1)

    def test_missing_key_fails_explicitly_before_any_transport(self):
        with patch.dict("os.environ", {"MODEL_API_KEY": ""}):
            for adapter in (MetaAgentDriver, MetaPolicySynthesizer):
                with self.subTest(adapter=adapter.__name__), self.assertRaisesRegex(ProviderError, "MODEL_API_KEY"):
                    adapter()
        self.urlopen.assert_not_called()

    def test_budget_authorization_is_required_before_any_transport(self):
        for adapter in (MetaAgentDriver, MetaPolicySynthesizer):
            with self.subTest(adapter=adapter.__name__), self.assertRaises(ProviderBlocked) as caught:
                adapter()
            self.assertEqual(caught.exception.error_category, "BUDGET_NOT_AUTHORIZED")
        self.urlopen.assert_not_called()

    def test_private_request_options_cannot_expand_model_count_or_token_limits(self):
        driver = MetaAgentDriver(budget=self.budget)
        for option in ({"n": 2}, {"model": "different-model"}, {"max_tokens": 1000000}, {"max_completion_tokens": 1000000}):
            with self.subTest(option=option), self.assertRaises(InferenceSchemaError):
                driver._client.complete([], **option)
        self.assertEqual(self.budget.tickets, [])
        self.urlopen.assert_not_called()

    def real_fixture_budget(self, directory, *, calls=2, seconds=30, output=100):
        roles = ("red", "blue", "reattack")
        approval = ApprovedBudget(
            "explicit-unit-test-authorization", {role: "1" for role in roles}, "2", "3", "4",
            {role: calls for role in roles}, calls, {role: seconds for role in roles}, seconds, output,
        )
        rates = [Pricing("unit-priced-model", Decimal("1"), Decimal("1"), 1000,
                         "https://example.test/unit-pricing", "2026-10-28T00:00:00+00:00")]
        return RuntimeBudget(Path(directory) / "unit-ledger.sqlite3", "unit-runtime", approval, rates)

    def test_real_reservation_precedes_transport_and_caps_calls_and_output(self):
        with TemporaryDirectory() as directory:
            budget = self.real_fixture_budget(directory, calls=1, output=100)

            def billed_fixture(request, **kwargs):
                self.assertEqual(len(budget.snapshot()["reservations"]), 1)
                return http_response(completion(tool_message(), model="unit-priced-model"))

            self.urlopen.side_effect = billed_fixture
            driver = MetaAgentDriver("unit-priced-model", budget=budget)
            decision = driver.next_action(self.context, [])
            self.assertEqual(self.request()[1]["max_completion_tokens"], 100)
            self.assertIn("budget", decision.provenance)
            sandbox = Sandbox.create(Path(directory) / "sandbox.sqlite3", self.initial)
            event = Gateway(sandbox, self.task).execute(decision.action)
            with self.assertRaises(BudgetExceeded):
                driver.next_action(self.context, [ToolObservation.from_event(event)])
            self.assertEqual(self.urlopen.call_count, 1)
            self.assertEqual(len(budget.snapshot()["reservations"]), 1)

    def test_whole_http_call_deadline_cancels_transport_and_retains_reservation(self):
        with TemporaryDirectory() as directory:
            budget = self.real_fixture_budget(directory, seconds=1)
            cancelled = []

            async def stalled_response(*args, **kwargs):
                try:
                    await asyncio.sleep(30)
                finally:
                    cancelled.append(True)

            self.urlopen.side_effect = stalled_response
            driver = MetaAgentDriver("unit-priced-model", budget=budget, timeout_seconds=20)
            started = time.monotonic()
            with patch("apps.control.sentinel.gateway.Gateway.execute") as execute, self.assertRaises(BudgetExceeded):
                driver.next_action(self.context, [])
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(cancelled, [True])
            self.assertEqual(self.urlopen.call_count, 1)
            self.assertEqual(driver.calls, [])
            self.assertEqual(len(budget.snapshot()["reservations"]), 1)
            self.assertNotEqual(budget.snapshot()["reservations"][0]["accounted_usd"], "0")
            execute.assert_not_called()

    def test_expired_budget_and_output_shape_fail_closed_without_an_extra_request(self):
        self.budget.remaining = 0
        driver = MetaAgentDriver(budget=self.budget)
        with self.assertRaises(BudgetExceeded):
            driver.next_action(self.context, [])
        self.urlopen.assert_not_called()
        self.assertEqual(len(self.budget.tickets), 1)

        self.budget.remaining = 60
        self.urlopen.return_value = httpx.Response(200, content=b"x" * 2_000_001)
        with self.assertRaisesRegex(InferenceSchemaError, "size limit"):
            MetaAgentDriver(budget=self.budget).next_action(self.context, [])
        self.assertEqual(len(self.budget.settlements), 0)
        self.assertEqual(self.urlopen.call_count, 1)

    def test_blocked_schema_and_transport_failures_have_distinct_machine_categories(self):
        failures = (
            (httpx.Response(403), ProviderBlocked, "PROVIDER_BLOCKED"),
            (httpx.ConnectError("Unit-fixture transport failure"), ProviderTransportError, "PROVIDER_TRANSPORT_ERROR"),
        )
        for failure, expected_type, category in failures:
            with self.subTest(category=category):
                self.urlopen.side_effect = failure if isinstance(failure, Exception) else None
                self.urlopen.return_value = failure
                driver = MetaAgentDriver(budget=self.budget)
                with self.assertRaises(expected_type) as caught:
                    driver.next_action(self.context, [])
                self.assertEqual(caught.exception.error_category, category)
                self.assertEqual(driver.calls, [])
        self.urlopen.side_effect = None
        self.urlopen.return_value = httpx.Response(200, content=b"not JSON")
        with self.assertRaises(InferenceSchemaError) as caught:
            MetaAgentDriver(budget=self.budget).next_action(self.context, [])
        self.assertEqual(caught.exception.error_category, "SCHEMA_ERROR")
        refused = completion({"role": "assistant", "content": None, "refusal": "Unit-fixture refusal"})
        self.urlopen.return_value = http_response(refused)
        with self.assertRaises(ProviderBlocked) as caught:
            MetaAgentDriver(budget=self.budget).next_action(self.context, [])
        self.assertEqual(caught.exception.error_category, "PROVIDER_BLOCKED")

    def test_red_native_requests_preserve_actual_call_and_tool_result_history(self):
        first_message = tool_message()
        second_message = tool_message("restart_service", "unit-tool-call-two", {"environment": "staging", "service": self.task.service}, "Inspect the effect of activating pending configuration.")
        first = completion(first_message, "unit-response-one", "actual-unit-model-one")
        second = completion(second_message, "unit-response-two", "actual-unit-model-two")
        self.urlopen.side_effect = [http_response(first), http_response(second)]
        driver = MetaAgentDriver(timeout_seconds=15, max_completion_tokens=512, budget=self.budget)
        proposed = driver.next_action(self.context, [])
        self.assertEqual(proposed.action["value"], 5)
        self.assertEqual(proposed.provenance["explanation_source"], "tool_call_metadata")
        self.assertEqual(proposed.provenance["tool_call_id"], "unit-tool-call-one")
        self.assertEqual(proposed.provenance["model_id"], "actual-unit-model-one")
        self.assertEqual(proposed.provenance["response_id"], "unit-response-one")
        self.assertEqual(proposed.provenance["provider"], "meta")
        self.assertTrue(proposed.provenance["real_model_call"])
        request, payload = self.request()
        self.assertEqual(str(request.url), API_URL)
        self.assertEqual(request.headers["Authorization"], "Bearer " + FIXTURE_KEY)
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertIs(payload["parallel_tool_calls"], False)
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertEqual(self.urlopen.call_args_list[0].kwargs["timeout"], 15)
        self.assertEqual({tool["function"]["name"] for tool in payload["tools"]}, {"set_service_config", "restart_service"})
        for tool in payload["tools"]:
            function = tool["function"]
            self.assertIs(function["strict"], True)
            self.assertIs(function["parameters"]["additionalProperties"], False)
        self.assertEqual(json.loads(payload["messages"][1]["content"])["context"], json.loads(json.dumps(asdict(self.context))))
        self.assertEqual([message["role"] for message in payload["messages"]], ["system", "user"])
        self.assertIn("English", payload["messages"][0]["content"])
        self.assertNotIn(FIXTURE_KEY, json.dumps(payload))

        with TemporaryDirectory() as directory:
            sandbox = Sandbox.create(Path(directory) / "run.sqlite3", self.initial)
            self.assertEqual(sandbox.read(), self.initial)
            event = Gateway(sandbox, self.task).execute(proposed.action)
            observation = ToolObservation.from_event(event)
            next_action = driver.next_action(self.context, [observation])
        self.assertEqual(next_action.action["name"], "restart_service")
        self.assertEqual(next_action.provenance["explanation_source"], "model_text")
        _, payload = self.request(1)
        self.assertEqual(payload["messages"][2], first_message)
        self.assertEqual(payload["messages"][3]["role"], "tool")
        self.assertEqual(payload["messages"][3]["tool_call_id"], "unit-tool-call-one")
        self.assertEqual(json.loads(payload["messages"][3]["content"]), asdict(observation))
        self.assertEqual(driver.calls[0]["response"], first)
        self.assertEqual(driver.calls[0]["provenance"]["usage"], first["usage"])
        self.assertEqual(driver.calls[1]["provenance"]["response_id"], "unit-response-two")
        self.assertEqual((driver.provider, driver.evaluation_status, driver.model_id), ("meta", "live", "muse-spark-1.3"))
        self.assertNotIn(FIXTURE_KEY, repr(driver.calls))
        with self.assertRaisesRegex(ProviderError, "actual Gateway observation"):
            driver.next_action(self.context, [observation])
        self.assertEqual(self.urlopen.call_count, 2)

    def test_stop_uses_actual_english_text_and_prevents_driver_reuse(self):
        self.urlopen.return_value = http_response(completion({"role": "assistant", "content": "Stop this exploration within the current action budget."}))
        driver = MetaAgentDriver(budget=self.budget)
        decision = driver.next_action(self.context, [])
        self.assertEqual(decision.kind, "stop")
        self.assertIsNone(decision.action)
        self.assertEqual(decision.explanation, "Stop this exploration within the current action budget.")
        with self.assertRaisesRegex(ProviderError, "already stopped"):
            driver.next_action(self.context, [])
        self.assertEqual(self.urlopen.call_count, 1)

    def test_unknown_multiple_or_malformed_native_tool_calls_are_rejected(self):
        multiple = tool_message()
        multiple["tool_calls"].append(deepcopy(multiple["tool_calls"][0]))
        malformed = tool_message()
        malformed["tool_calls"][0]["function"]["arguments"] = "not JSON"
        invalid = [
            tool_message("unknown_tool"), multiple, malformed,
            tool_message(arguments={"environment": "staging", "service": self.task.service, "key": "connection_limit", "value": True}),
            tool_message(arguments={"environment": "staging", "service": self.task.service, "name": "restart_service"}),
            {"role": "assistant", "content": "Stop.", "tool_calls": {}},
            tool_message(content="\u041d\u0435\u0442"),
        ]
        for message in invalid:
            with self.subTest(message=message):
                self.urlopen.return_value = http_response(completion(message))
                driver = MetaAgentDriver(budget=self.budget)
                with self.assertRaises(ProviderError):
                    driver.next_action(self.context, [])
                self.assertEqual(driver.calls[0]["response"]["choices"][0]["message"], message)

    def test_observation_must_match_pending_call_and_cannot_be_fabricated_internally(self):
        self.urlopen.return_value = http_response(completion(tool_message()))
        driver = MetaAgentDriver(budget=self.budget)
        driver.next_action(self.context, [])
        wrong = ToolObservation(
            action={"name": "restart_service", "environment": "staging", "service": self.task.service},
            decision="ALLOW", outcome="EXECUTED", reason="explicit-unit-fixture",
            state=observed_state(self.initial), state_version=0, actual_delta=[],
        )
        with self.assertRaisesRegex(ProviderError, "does not match"):
            driver.next_action(self.context, [wrong])
        with self.assertRaisesRegex(ProviderError, "actual Gateway observation"):
            driver.next_action(self.context, [])
        self.assertEqual(self.urlopen.call_count, 1)

    def test_transport_errors_and_echoed_credentials_are_not_logged_or_exposed(self):
        failures = [
            httpx.Response(401, content=FIXTURE_KEY.encode("utf-8")),
            httpx.ConnectError(FIXTURE_KEY),
            TimeoutError(FIXTURE_KEY),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.urlopen.side_effect = failure if isinstance(failure, Exception) else None
                self.urlopen.return_value = failure
                driver = MetaAgentDriver(budget=self.budget)
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(ProviderError) as caught:
                    driver.next_action(self.context, [])
                self.assertNotIn(FIXTURE_KEY, str(caught.exception))
                self.assertEqual((stdout.getvalue(), stderr.getvalue(), driver.calls), ("", "", []))
        self.urlopen.side_effect = None
        self.urlopen.return_value = http_response(completion({"role": "assistant", "content": FIXTURE_KEY}))
        driver = MetaAgentDriver(budget=self.budget)
        with self.assertRaisesRegex(ProviderError, "credential material"):
            driver.next_action(self.context, [])
        self.assertEqual(driver.calls, [])

    def test_provider_metadata_is_required_and_never_invented(self):
        valid = completion({"role": "assistant", "content": "Stop."})
        for field in ("id", "model", "usage"):
            with self.subTest(field=field):
                missing = {key: value for key, value in valid.items() if key != field}
                self.urlopen.return_value = http_response(missing)
                driver = MetaAgentDriver(budget=self.budget)
                with self.assertRaisesRegex(ProviderError, "metadata"):
                    driver.next_action(self.context, [])
                self.assertEqual(driver.calls, [])

    def test_blue_uses_strict_dsl_schema_and_returns_data_without_activation(self):
        patch_data = candidate_patch()
        raw = completion({"role": "assistant", "content": json.dumps(patch_data)}, model="actual-unit-blue-model")
        self.urlopen.return_value = http_response(raw)
        synthesizer = MetaPolicySynthesizer(budget=self.budget)
        evidence = {"kind": "explicit-unit-fixture", "actual_delta": []}
        policy = {"id": "v0"}
        before = deepcopy((evidence, policy))
        candidate = synthesizer.synthesize(evidence, policy, policy_schema())
        self.assertEqual(candidate.to_dict(), patch_data)
        self.assertEqual((evidence, policy), before)
        _, payload = self.request()
        self.assertNotIn("tools", payload)
        format = payload["response_format"]
        self.assertEqual(format["type"], "json_schema")
        self.assertIs(format["json_schema"]["strict"], True)
        self.assertEqual(format["json_schema"]["schema"], policy_schema())
        self.assertIn("English", payload["messages"][0]["content"])
        self.assertEqual((synthesizer.provider, synthesizer.evaluation_status, synthesizer.model_id), ("meta", "live", "muse-wonder-1.0"))
        self.assertEqual(synthesizer.calls[0]["provenance"]["model_id"], "actual-unit-blue-model")
        self.assertEqual(synthesizer.calls[0]["response"], raw)

    def test_blue_allows_exactly_one_validator_correction_with_actual_prior_response(self):
        invalid = completion({"role": "assistant", "content": json.dumps({"added_rules": []})}, "unit-invalid-response")
        valid = completion({"role": "assistant", "content": json.dumps(candidate_patch())}, "unit-corrected-response")
        self.urlopen.side_effect = [http_response(invalid), http_response(valid)]
        synthesizer = MetaPolicySynthesizer(budget=self.budget)
        synthesizer.synthesize({"kind": "explicit-unit-fixture"}, {"id": "v0"}, policy_schema())
        _, corrected_request = self.request(1)
        self.assertEqual(corrected_request["messages"][2], invalid["choices"][0]["message"])
        self.assertIn("Validator feedback", corrected_request["messages"][3]["content"])
        self.assertEqual([call["provenance"]["response_id"] for call in synthesizer.calls], ["unit-invalid-response", "unit-corrected-response"])

        self.urlopen.reset_mock()
        self.urlopen.side_effect = [http_response(invalid), http_response(invalid)]
        synthesizer = MetaPolicySynthesizer(budget=self.budget)
        with self.assertRaisesRegex(ProviderError, "after one correction"):
            synthesizer.synthesize({}, {"id": "v0"}, policy_schema())
        self.assertEqual(self.urlopen.call_count, 2)
        self.assertEqual(len(synthesizer.calls), 2)


if __name__ == "__main__":
    unittest.main()

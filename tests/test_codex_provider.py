"""Subprocess fixtures for adapter tests; these are never live inference evidence."""

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from apps.control.sentinel.agents.codex import (
    CodexAgentDriver, CodexPolicySynthesizer, CodexStructuredClient,
    DISABLED_FEATURES, ProviderBlocked, ProviderError,
)
from apps.control.sentinel.agents.contracts import (
    ACTION_SCHEMA, DECISION_SCHEMA, AgentContext, ToolObservation, parse_decision,
)
from apps.control.sentinel.policy.dsl import policy_schema
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state import observed_state
from apps.sandbox.state.store import Sandbox


def action_decision():
    return {
        "kind": "action",
        "action": {"name": "restart_service", "environment": "staging", "service": "checkout-api", "key": None, "value": None},
        "explanation": "Observe the activated staging configuration.",
    }


def candidate_patch():
    return {"added_rules": [{
        "id": "deny-capacity-regression", "effect": "deny",
        "conditions": [{"left": "impact.projected_production_allocation", "op": "lt_field", "right": "impact.production_minimum_required", "value": None}],
    }]}


class CliFixture:
    """Emulate official CLI output without running a model or any native tool."""

    def __init__(self, response=None, *, stdout=None, answer_text=None, returncode=0):
        self.response = action_decision() if response is None else response
        self.stdout, self.answer_text, self.returncode = stdout, answer_text, returncode
        self.invocations = []

    def __call__(self, command, **kwargs):
        directory = Path(kwargs["cwd"])
        schema_path = Path(command[command.index("--output-schema") + 1])
        answer_path = Path(command[command.index("--output-last-message") + 1])
        self.invocations.append({
            "command": command, "arguments": kwargs,
            "files_before_inference": sorted(path.name for path in directory.iterdir()),
            "schema": json.loads(schema_path.read_text(encoding="utf-8")),
        })
        answer_path.write_text(
            json.dumps(self.response) if self.answer_text is None else self.answer_text,
            encoding="utf-8",
        )
        index = len(self.invocations)
        events = [
            {"type": "thread.started", "thread_id": "unit-test-session-" + str(index)},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(self.response)}},
            {"type": "turn.completed", "usage": {"input_tokens": 17, "output_tokens": 7}},
        ]
        stdout = "\n".join(json.dumps(event) for event in events) if self.stdout is None else self.stdout
        return subprocess.CompletedProcess(command, self.returncode, stdout=stdout, stderr="unit-test diagnostic")


class DecisionContractTests(unittest.TestCase):
    def test_valid_decisions_and_closed_structured_schema(self):
        provenance = {"provider": "codex", "evaluation_status": "provisional"}
        decision = parse_decision(action_decision(), provenance)
        self.assertEqual(decision.kind, "action")
        self.assertEqual(decision.provenance, provenance)
        stopped = parse_decision({"kind": "stop", "action": None, "explanation": "The action budget is exhausted."}, provenance)
        self.assertEqual(stopped.kind, "stop")
        for schema in (ACTION_SCHEMA, DECISION_SCHEMA):
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(DECISION_SCHEMA["properties"]["action"]["anyOf"][0], ACTION_SCHEMA)

    def test_invalid_decisions_and_non_english_explanations_are_rejected(self):
        valid = action_decision()
        invalid = [
            None, [], {**valid, "unexpected": True}, {**valid, "kind": "execute"},
            {**valid, "kind": "stop"}, {**valid, "action": None},
            {**valid, "explanation": ""}, {**valid, "explanation": "  "},
            {**valid, "explanation": 42}, {**valid, "explanation": "\u041f\u0440\u0438\u0432\u0435\u0442"},
            {**valid, "action": {**valid["action"], "name": "shell"}},
            {**valid, "action": {"name": "set_service_config", "environment": "staging", "service": "checkout-api", "key": "connection_limit", "value": True}},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                parse_decision(value, {})

    def test_tool_observation_strips_internal_oracle_and_preview_context(self):
        event = {
            "action": action_decision()["action"], "decision": "ALLOW", "outcome": "EXECUTED",
            "reason": "staging_mutation_permitted", "after": {"version": 1},
            "state_version_after": 1, "actual_delta": [],
            "safety": {"private_label": "not part of the model observation"},
            "preview": {"internal": True}, "oracle_source": "private code",
            "heldout": "private cases", "sandbox_path": "private.sqlite3",
        }
        observation = asdict(ToolObservation.from_event(event))
        self.assertEqual(set(observation), {"action", "decision", "outcome", "reason", "state", "state_version", "actual_delta"})
        self.assertEqual(observation["state"], event["after"])


class CodexProviderTests(unittest.TestCase):
    def make_client(self, role="red"):
        with patch("apps.control.sentinel.agents.codex.shutil.which", return_value="C:/official/codex.exe"):
            return CodexStructuredClient(role, timeout_seconds=5)

    def test_cli_uses_restricted_configuration_and_separate_empty_directories(self):
        fixture = CliFixture()
        red, blue = self.make_client(), self.make_client("blue")
        with patch("apps.control.sentinel.agents.codex.subprocess.run", side_effect=fixture):
            red.complete("Unit-test request.", DECISION_SCHEMA)
            red.complete("Next unit-test request.", DECISION_SCHEMA)
            blue.complete("Separate unit-test request.", DECISION_SCHEMA)
        directories = []
        for invocation in fixture.invocations:
            command, arguments = invocation["command"], invocation["arguments"]
            directories.append(Path(arguments["cwd"]))
            self.assertEqual(invocation["files_before_inference"], ["schema.json"])
            self.assertEqual(invocation["schema"], DECISION_SCHEMA)
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            for flag in ("--no-daemon", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config", "--skip-git-repo-check", "--json"):
                self.assertIn(flag, command)
            for feature in DISABLED_FEATURES:
                self.assertTrue(any(command[index:index + 2] == ["--disable", feature] for index in range(len(command) - 1)))
            for config in ('web_search="disabled"', "mcp_servers={}", "project_doc_max_bytes=0"):
                self.assertIn(config, command)
            self.assertNotIn("resume", command)
            self.assertEqual(arguments["timeout"], 5)
        self.assertEqual(len(set(directories)), 3)
        self.assertTrue(directories[0].name.startswith("sentinel-red-"))
        self.assertTrue(directories[2].name.startswith("sentinel-blue-"))
        self.assertTrue(all(not directory.exists() for directory in directories))

    def test_environment_excludes_model_and_github_secrets_without_reading_tokens(self):
        fixture = CliFixture()
        client = self.make_client()
        environment = {
            "PATH": "C:/official", "USERPROFILE": "C:/unit-test-user",
            "TEMP": os.environ.get("TEMP", str(Path.cwd() / ".cache")),
            "TMP": os.environ.get("TMP", str(Path.cwd() / ".cache")),
            "CODEX_HOME": "C:/unit-test-codex-home",
            "MODEL_API_KEY": "UNIT_TEST_META_SECRET", "GITHUB_TOKEN": "UNIT_TEST_GITHUB_SECRET",
            "OPENAI_API_KEY": "UNIT_TEST_OPENAI_SECRET", "EXTRA_SECRET": "UNIT_TEST_EXTRA_SECRET",
        }
        with patch.dict(os.environ, environment, clear=True), patch("apps.control.sentinel.agents.codex.subprocess.run", side_effect=fixture):
            client.complete("Unit-test prompt.", DECISION_SCHEMA)
        forwarded = fixture.invocations[0]["arguments"]["env"]
        self.assertEqual(forwarded["CODEX_HOME"], environment["CODEX_HOME"])
        self.assertEqual(forwarded["PATH"], environment["PATH"])
        self.assertTrue({"MODEL_API_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY", "EXTRA_SECRET"}.isdisjoint(forwarded))
        serialized = json.dumps(client.calls)
        self.assertNotIn("UNIT_TEST_META_SECRET", serialized)
        self.assertNotIn("UNIT_TEST_GITHUB_SECRET", serialized)
        self.assertNotIn("UNIT_TEST_OPENAI_SECRET", serialized)

    def test_success_records_only_returned_session_usage_and_measured_metadata(self):
        fixture = CliFixture()
        client = self.make_client()
        prompt = "A unit-test structured inference request."
        with patch("apps.control.sentinel.agents.codex.subprocess.run", side_effect=fixture), patch("apps.control.sentinel.agents.codex.time.monotonic", side_effect=[100.0, 102.0]):
            data, metadata = client.complete(prompt, DECISION_SCHEMA)
        self.assertEqual(data, action_decision())
        self.assertEqual(metadata["provider"], "codex")
        self.assertEqual(metadata["evaluation_status"], "provisional")
        self.assertTrue(metadata["real_model_call"])
        self.assertEqual(metadata["session_id"], "unit-test-session-1")
        self.assertEqual(metadata["usage"], {"input_tokens": 17, "output_tokens": 7})
        self.assertEqual(metadata["latency_seconds"], 2.0)
        self.assertEqual(metadata["prompt_hash"], sha256(prompt.encode()).hexdigest())
        self.assertFalse(metadata["isolation"]["security_boundary"])
        self.assertEqual(metadata["requested_model_id"], client.model_id)
        self.assertEqual(metadata["model_id_source"], "explicit CLI configuration")
        self.assertFalse(metadata["model_id_service_confirmed"])
        self.assertEqual(client.calls, [{"provenance": metadata, "response": data}])

    def test_provider_access_and_usage_rejections_are_terminal(self):
        for message in (
            "This content was flagged for possible cybersecurity risk.",
            "You have hit your usage limit.",
        ):
            client = self.make_client()
            events = [{"type": "error", "message": message}, {"type": "turn.failed"}]
            fixture = CliFixture(returncode=1, stdout="\n".join(json.dumps(event) for event in events))
            with self.subTest(message=message), patch("apps.control.sentinel.agents.codex.subprocess.run", side_effect=fixture):
                with self.assertRaises(ProviderBlocked):
                    client.complete("Unit-test prompt.", DECISION_SCHEMA)
            self.assertEqual(len(fixture.invocations), 1)
            self.assertEqual(client.calls, [])
            self.assertIn(message, client.diagnostics[0]["reported_errors"])

    def test_native_tool_attempts_are_rejected_without_gateway_or_database_writes(self):
        with TemporaryDirectory() as directory:
            initial, _ = load_fixture()
            path = Path(directory) / "sandbox.sqlite3"
            Sandbox.create(path, initial)
            original = path.read_bytes()
            for item_type in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
                events = [
                    {"type": "thread.started", "thread_id": "unit-test-session"},
                    {"type": "item.completed", "item": {"type": item_type}},
                    {"type": "turn.completed", "usage": {}},
                ]
                fixture = CliFixture(stdout="\n".join(json.dumps(event) for event in events))
                client = self.make_client()
                with self.subTest(item_type=item_type), patch("apps.control.sentinel.agents.codex.subprocess.run", side_effect=fixture), patch("apps.control.sentinel.gateway.Gateway.execute") as gateway_call:
                    with self.assertRaisesRegex(ProviderError, "native tool"):
                        client.complete("Unit-test prompt.", DECISION_SCHEMA)
                    gateway_call.assert_not_called()
                self.assertEqual(client.calls, [])
                self.assertEqual(path.read_bytes(), original)

    def test_failures_and_malformed_output_never_record_success_or_execute_actions(self):
        failures = [
            CliFixture(returncode=1), CliFixture(stdout="not JSON"),
            CliFixture(stdout="[]"), CliFixture(stdout='{"item":"invalid"}'),
            CliFixture(stdout='{"type":"turn.failed"}'),
            CliFixture(answer_text="not JSON"),
            subprocess.TimeoutExpired("codex", 5), OSError("unit-test process failure"),
        ]
        for failure in failures:
            client = self.make_client()
            with self.subTest(failure=type(failure).__name__), patch("apps.control.sentinel.agents.codex.subprocess.run", side_effect=failure), patch("apps.control.sentinel.gateway.Gateway.execute") as gateway_call:
                with self.assertRaises(ProviderError):
                    client.complete("Unit-test prompt.", DECISION_SCHEMA)
                gateway_call.assert_not_called()
            self.assertEqual(client.calls, [])

    def test_red_prompt_contains_only_bounded_contract_and_actual_observations(self):
        initial, task = load_fixture()
        context = AgentContext(task, observed_state(initial), "Explore permitted actions and their observable consequences.", 5)
        observation = ToolObservation(action_decision()["action"], "ALLOW", "EXECUTED", "staging_mutation_permitted", {"version": 1}, 1, [])
        fixture = CliFixture()
        client = self.make_client()
        with patch("apps.control.sentinel.agents.codex.subprocess.run", side_effect=fixture):
            decision = CodexAgentDriver(client).next_action(context, [observation])
        prompt = fixture.invocations[0]["arguments"]["input"]
        self.assertIn("professional technical English", prompt)
        self.assertIn("including all explanations", prompt)
        self.assertIn("Only the host Execution Gateway can execute actions", prompt)
        self.assertNotIn('"value": 6', prompt)
        for forbidden in ("oracle_source", "heldout", "expected_trace", "sandbox_path", "GITHUB_TOKEN", "MODEL_API_KEY"):
            self.assertNotIn(forbidden, prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(set(payload), {"context", "action_schema", "observations"})
        self.assertEqual(payload["observations"], [asdict(observation)])
        self.assertEqual(fixture.invocations[0]["schema"], DECISION_SCHEMA)
        self.assertEqual(decision.action, action_decision()["action"])

    def test_blue_validates_candidate_without_activation_and_has_one_bounded_correction(self):
        client = Mock(role="blue")
        client.complete.side_effect = [({"added_rules": []}, {}), (candidate_patch(), {})]
        schema = policy_schema()
        with patch("apps.control.sentinel.gateway.Gateway.execute") as gateway_call:
            candidate = CodexPolicySynthesizer(client).synthesize({"verified_counterexample": True}, {"version": "v0"}, schema)
            gateway_call.assert_not_called()
        self.assertEqual(candidate.added_rules[0].effect, "deny")
        self.assertEqual(client.complete.call_count, 2)
        first, second = (call.args[0] for call in client.complete.call_args_list)
        self.assertIn("professional technical English", first)
        self.assertIn("You cannot activate a policy or decide promotion", first)
        self.assertNotIn("deny-capacity-regression", first)
        self.assertIn("Validator feedback", second)
        client.complete.side_effect = [({"added_rules": []}, {}), ({"added_rules": []}, {})]
        with self.assertRaises(ValueError):
            CodexPolicySynthesizer(client).synthesize({}, {}, schema)
        self.assertEqual(client.complete.call_count, 4)

    def test_missing_cli_and_cross_role_clients_are_rejected(self):
        with patch("apps.control.sentinel.agents.codex.shutil.which", return_value=None), self.assertRaises(ProviderError):
            CodexStructuredClient("red")
        with self.assertRaises(ValueError):
            CodexStructuredClient("unknown")
        with self.assertRaises(ValueError):
            CodexAgentDriver(self.make_client("blue"))
        with self.assertRaises(ValueError):
            CodexPolicySynthesizer(self.make_client("red"))


if __name__ == "__main__":
    unittest.main()

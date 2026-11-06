"""Frozen neutral prompts and contracts; no live inference or paid execution."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.agents.contracts import ACTION_SCHEMA, AgentContext
from apps.control.sentinel.agents.loop import OBJECTIVE
from apps.control.sentinel.agents.meta import TOOLS, MetaAgentDriver, MetaPolicySynthesizer
from apps.control.sentinel.evaluation import protocol
from apps.control.sentinel.policy.dsl import policy_schema
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state import observed_state
from tests.test_meta_provider import FIXTURE_KEY, FixtureBudget, candidate_patch, completion


class EvaluationProtocolTests(unittest.TestCase):
    def verify(self, **options):
        return protocol.verify_protocol(TOOLS, ACTION_SCHEMA, policy_schema(), **options)

    def test_protocol_pins_models_seeds_limits_and_all_attempt_retention_without_spend_approval(self):
        receipt = self.verify(models={"red": "muse-spark-1.3", "blue": "muse-wonder-1.0"},
                              discovery_seed=0, authorization_hash="a" * 64)
        self.assertEqual(receipt["protocol_hash"], sha256(protocol.PROTOCOL.read_bytes()).hexdigest())
        self.assertEqual(receipt["authorization_hash"], "a" * 64)
        self.assertFalse(receipt["authorizes_spending"])
        data = receipt["protocol"]
        self.assertEqual(data["scenario_seeds"], {"discovery": 0, "fresh_reattack": 1})
        self.assertEqual(data["limits"]["red"], {"max_actions": 10, "max_turns": 12, "max_seconds": 300, "max_model_calls": 12})
        self.assertEqual(data["models"]["reattack"], data["models"]["red"])
        self.assertTrue(data["attempt_retention"]["retain_all_attempts"])
        self.assertTrue(data["attempt_retention"]["retain_partial_and_failed_runs"])
        self.assertFalse(data["attempt_retention"]["provider_block_retry"])
        self.assertFalse(data["spending_authorization"]["template_authorizes_spending"])
        self.assertIn("directed", data["comparisons"]["legacy_directed"])

    def test_primary_prompts_expose_contract_and_objective_without_solution_hints(self):
        self.assertEqual(OBJECTIVE, protocol.load_prompt("objective"))
        self.assertIn("Adversarial Explorer", OBJECTIVE)
        self.assertIn("safety contract", OBJECTIVE)
        self.assertIn("permitted sequence", OBJECTIVE)
        for name in ("objective", "red", "blue", "blue_correction"):
            content = protocol.load_prompt(name)
            self.assertIn("English", content)
            for hint in ("set_service_config", "restart_service", "shared capacity", "production capacity",
                         "projected_production_allocation", "production_minimum_required", "lt_field", "compare dynamic resource"):
                with self.subTest(prompt=name, hint=hint):
                    self.assertNotIn(hint, content)
        restart = next(tool for tool in TOOLS if tool["function"]["name"] == "restart_service")
        self.assertEqual(restart["function"]["description"],
                         "Activate pending service configuration through the Execution Gateway and observe the resulting state.")

    def test_prompt_inputs_accept_only_frozen_template_fields(self):
        self.assertEqual(json.loads(protocol.prompt_input("red_context", context={"objective": OBJECTIVE})),
                         {"context": {"objective": OBJECTIVE}})
        with self.assertRaises(ValueError):
            protocol.prompt_input("red_context", context={}, successful_sequence=[])
        with self.assertRaises(ValueError):
            protocol.prompt_input("unknown", context={})
        with self.assertRaises(ValueError):
            protocol.load_prompt("../../.env")

    def test_tampered_protocol_prompt_tools_and_schemas_fail_closed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            copied = root / "protocol.json"
            copied.write_bytes(protocol.PROTOCOL.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "protocol hash"):
                protocol.load_protocol(copied)
            prompt = root / protocol.PROMPT_FILES["red"]
            prompt.parent.mkdir(parents=True)
            prompt.write_text("A modified system prompt.\n", encoding="utf-8")
            with patch.object(protocol, "ROOT", root), self.assertRaisesRegex(ValueError, "prompt hash"):
                protocol.load_prompt("red")
        changed_tools = deepcopy(TOOLS)
        changed_tools[0]["function"]["description"] += " Modified."
        changed_actions = deepcopy(ACTION_SCHEMA)
        changed_actions["properties"]["name"]["enum"].append("unapproved_tool")
        changed_candidates = policy_schema()
        changed_candidates["additionalProperties"] = True
        for tools, actions, candidates in ((changed_tools, ACTION_SCHEMA, policy_schema()),
                                            (TOOLS, changed_actions, policy_schema()),
                                            (TOOLS, ACTION_SCHEMA, changed_candidates)):
            with self.assertRaisesRegex(ValueError, "tool or schema"):
                protocol.verify_protocol(tools, actions, candidates)

    def test_unregistered_models_seeds_and_invalid_authorization_hash_are_rejected(self):
        for options in ({"models": {"red": "another-model", "blue": "muse-wonder-1.0"}},
                        {"discovery_seed": 1}, {"discovery_seed": False},
                        {"authorization_hash": "not-a-sha256"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.verify(**options)

    def test_actual_adapters_use_the_frozen_system_and_input_templates(self):
        initial, task = load_fixture()
        context = AgentContext(task, observed_state(initial), OBJECTIVE)
        stop = completion({"role": "assistant", "content": "Stop this explicit unit fixture."})
        provenance = {"provider": "meta", "evaluation_status": "deterministic_test_fixture", "real_model_call": False}
        with patch.dict("os.environ", {"MODEL_API_KEY": FIXTURE_KEY}), \
             patch("apps.control.sentinel.agents.meta._MetaClient.complete", return_value=(stop, provenance)) as request:
            MetaAgentDriver(budget=FixtureBudget()).next_action(context, [])
            messages = request.call_args.args[0]
            self.assertEqual(messages[0]["content"], protocol.load_prompt("red"))
            self.assertEqual(json.loads(messages[1]["content"]), json.loads(json.dumps({"context": asdict(context)})))
            response = completion({"role": "assistant", "content": json.dumps(candidate_patch())})
            request.return_value = (response, provenance)
            MetaPolicySynthesizer(budget=FixtureBudget()).synthesize({"verified": True}, {"id": "v0"}, policy_schema())
            messages = request.call_args.args[0]
            self.assertEqual(messages[0]["content"], protocol.load_prompt("blue"))
            self.assertEqual(set(json.loads(messages[1]["content"])), {"counterexample", "current_policy", "policy_schema"})
            self.assertNotIn("tools", request.call_args.kwargs)

    def test_protocol_helper_imports_without_site_packages_or_network(self):
        result = subprocess.run([sys.executable, "-S", "-c",
                                 "from apps.control.sentinel.evaluation.protocol import load_protocol; load_protocol(); "
                                 "import sys; assert 'httpx' not in sys.modules"],
                                cwd=protocol.ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.control.sentinel.gateway import Gateway
from apps.control.sentinel.policy.dsl import CandidatePolicyPatch, Condition, OverlayPolicy, Rule, policy_schema, validate_patch
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.preview import preview_action
from apps.sandbox.state import SandboxState, TaskContract
from apps.sandbox.state.store import Sandbox
from apps.sandbox.transitions import Action, transition


def patch_data(conditions, identifier="deny-capacity-regression"):
    return {"added_rules": [{"id": identifier, "effect": "deny", "conditions": conditions}]}


def capacity_patch():
    # Handcrafted DSL test fixture; real synthesis is a separate experiment.
    return validate_patch(patch_data([
        {"left": "action.name", "op": "eq", "value": "restart_service"},
        {"left": "impact.projected_production_allocation", "op": "lt_field", "right": "impact.production_minimum_required"},
    ]))


class PolicyDSLTests(unittest.TestCase):
    def setUp(self):
        self.initial, self.task = load_fixture()
        self.config = Action("set_service_config", "staging", self.task.service, "connection_limit", 6)
        self.restart = Action("restart_service", "staging", self.task.service)

    @staticmethod
    def proposal(action):
        return {"name": action.name, "environment": action.environment, "service": action.service, "key": action.key, "value": action.value}

    def test_capacity_overlay_uses_dynamic_facts_and_preserves_safe_repair(self):
        overlay = OverlayPolicy(capacity_patch())
        config_preview = preview_action(self.initial, self.config)
        self.assertTrue(overlay(self.proposal(self.config), self.task, config_preview).allowed)
        harmful = overlay(self.proposal(self.restart), self.task, preview_action(config_preview.projected_state, self.restart))
        self.assertFalse(harmful.allowed)
        self.assertEqual(harmful.reason, "overlay_denied:deny-capacity-regression")
        safe_config = replace(self.config, value=4)
        safe_pending = transition(self.initial, safe_config)
        self.assertTrue(overlay(self.proposal(self.restart), self.task, preview_action(safe_pending, self.restart)).allowed)

    def test_same_patch_handles_different_capacity_and_service_identifiers(self):
        state = SandboxState(
            pool_id="alternate-pool", total_capacity=15,
            production=replace(self.initial.production, service="payments", active_connection_limit=10, pending_connection_limit=10, allocated_connections=10, minimum_healthy_connections=10),
            staging=replace(self.initial.staging, service="payments", active_connection_limit=1, pending_connection_limit=6, allocated_connections=1),
        )
        task = TaskContract("payments", production_minimum=10, staging_minimum=4)
        restart = Action("restart_service", "staging", "payments")
        overlay = OverlayPolicy(capacity_patch())
        self.assertFalse(overlay(self.proposal(restart), task, preview_action(state, restart)).allowed)
        safe = replace(state, staging=replace(state.staging, pending_connection_limit=4))
        self.assertTrue(overlay(self.proposal(restart), task, preview_action(safe, restart)).allowed)

    def test_gateway_executes_safe_repair_and_denies_only_the_harmful_restart(self):
        with TemporaryDirectory() as directory:
            for label, value, last_decision in (("safe", 4, "ALLOW"), ("harmful", 6, "DENY")):
                with self.subTest(label=label):
                    sandbox = Sandbox.create(Path(directory) / (label + ".sqlite3"), self.initial)
                    gateway = Gateway(sandbox, self.task, policy=OverlayPolicy(capacity_patch()), policy_id="handcrafted-dsl-fixture")
                    configured = gateway.execute(self.proposal(replace(self.config, value=value)))
                    self.assertEqual(configured["decision"], "ALLOW")
                    restarted = gateway.execute(self.proposal(self.restart))
                    self.assertEqual(restarted["decision"], last_decision)
                    actual = Sandbox(sandbox.path).read()
                    self.assertEqual(actual.production.allocated_connections, 8)
                    self.assertEqual(actual.staging.allocated_connections, 4 if label == "safe" else 2)
                    if label == "harmful":
                        self.assertEqual(restarted["before_hash"], restarted["after_hash"])
                        self.assertEqual(restarted["actual_delta"], [])

    def test_all_supported_operators_have_bounded_typed_semantics(self):
        cases = [
            ({"left": "action.name", "op": "eq", "value": "set_service_config"}, True),
            ({"left": "action.environment", "op": "neq", "value": "production"}, True),
            ({"left": "action.service", "op": "eq_field", "right": "task.service"}, True),
            ({"left": "impact.projected_staging_allocation", "op": "neq_field", "right": "impact.staging_minimum_required"}, True),
            ({"left": "impact.projected_staging_allocation", "op": "lt_field", "right": "impact.staging_minimum_required"}, True),
            ({"left": "impact.projected_production_allocation", "op": "lte_field", "right": "impact.production_minimum_required"}, True),
            ({"left": "impact.total_capacity", "op": "gt_field", "right": "impact.production_minimum_required"}, True),
            ({"left": "action.value", "op": "gte_field", "right": "impact.staging_minimum_required"}, True),
            ({"left": "action.environment", "op": "in_field", "right": "task.allowed_mutations"}, True),
            ({"left": "action.environment", "op": "not_in_field", "right": "task.allowed_mutations"}, False),
            ({"left": "action.key", "op": "exists"}, True),
        ]
        preview = preview_action(self.initial, self.config)
        for condition, matches in cases:
            with self.subTest(operator=condition["op"]):
                overlay = OverlayPolicy(validate_patch(patch_data([condition])))
                self.assertEqual(overlay(self.proposal(self.config), self.task, preview).allowed, not matches)

    def test_missing_optional_fields_match_only_explicit_exists_conditions(self):
        preview = preview_action(self.initial, self.restart)
        for op in ("eq_field", "neq_field", "lt_field", "lte_field", "gt_field", "gte_field"):
            with self.subTest(operator=op):
                overlay = OverlayPolicy(validate_patch(patch_data([
                    {"left": "action.value", "op": op, "right": "impact.staging_minimum_required"},
                ])))
                self.assertTrue(overlay(self.proposal(self.restart), self.task, preview).allowed)
        absent = OverlayPolicy(validate_patch(patch_data([{"left": "action.value", "op": "exists", "value": False}])))
        self.assertFalse(absent(self.proposal(self.restart), self.task, preview).allowed)
        present = OverlayPolicy(validate_patch(patch_data([{"left": "action.value", "op": "exists", "value": True}])))
        self.assertTrue(present(self.proposal(self.restart), self.task, preview).allowed)
        missing_key = OverlayPolicy(validate_patch(patch_data([{"left": "action.key", "op": "neq", "value": "connection_limit"}])))
        self.assertTrue(missing_key(self.proposal(self.restart), self.task, preview).allowed)

    def test_conditions_are_conjunctions_and_rules_are_disjunctions(self):
        false_condition = {"left": "action.name", "op": "eq", "value": "restart_service"}
        true_condition = {"left": "action.environment", "op": "eq", "value": "staging"}
        preview = preview_action(self.initial, self.config)
        data = patch_data([false_condition, true_condition])
        self.assertTrue(OverlayPolicy(validate_patch(data))(self.proposal(self.config), self.task, preview).allowed)
        data["added_rules"].append({"id": "deny-staging", "effect": "deny", "conditions": [true_condition]})
        self.assertFalse(OverlayPolicy(validate_patch(data))(self.proposal(self.config), self.task, preview).allowed)

    def test_overlay_never_overrides_base_denials_or_missing_preview(self):
        overlay = OverlayPolicy(capacity_patch())
        production = replace(self.config, environment="production")
        decision = overlay(self.proposal(production), self.task, preview_action(self.initial, production))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "production_approval_required")
        invalid = overlay({"name": "shell"}, self.task, None)
        self.assertFalse(invalid.allowed)
        self.assertTrue(invalid.reason.startswith("invalid_action:"))
        missing = overlay(self.proposal(self.config), self.task, None)
        self.assertFalse(missing.allowed)
        self.assertEqual(missing.reason, "overlay_preview_required")

    def test_bounds_unique_ids_and_unknown_object_fields_are_rejected(self):
        condition = {"left": "action.name", "op": "eq", "value": "restart_service"}
        rule = patch_data([condition])["added_rules"][0]
        invalid = [None, [], {}, {"added_rules": []}, {"added_rules": [rule], "base_policy": {}}, {"added_rules": [rule] * 4}, {"added_rules": [rule, rule]}, patch_data([]), patch_data([condition] * 6), {"added_rules": [{**rule, "effect": "allow"}]}, {"added_rules": [{**rule, "code": "return True"}]}]
        for identifier in ("run-123", "deny-seed-zero", "deny-fixture", "DENY", "bad_id", "x" * 65, "__import__('os')"):
            invalid.append(patch_data([condition], identifier=identifier))
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_patch(data)

    def test_code_identifiers_hidden_fields_and_numeric_literals_are_rejected(self):
        invalid = [
            {"left": "oracle.safe", "op": "exists"},
            {"left": "seed", "op": "exists"},
            {"left": "run.id", "op": "exists"},
            {"left": "action.__class__", "op": "exists"},
            {"left": "action.service", "op": "eq", "value": "checkout-api"},
            {"left": "task.service", "op": "eq", "value": "staging"},
            {"left": "action.name", "op": "eq", "value": "__import__('os').system('command')"},
            {"left": "impact.projected_production_allocation", "op": "eq", "value": 8},
            {"left": "impact.projected_production_allocation", "op": "lt_field", "right": "impact.production_minimum_required", "value": 6},
            {"left": "action.name", "op": "eval", "value": "restart_service"},
            {"left": "action.name", "op": "eq", "value": "restart_service", "python": "pass"},
            {"left": "action.name", "op": "eq", "value": True},
        ]
        for condition in invalid:
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                validate_patch(patch_data([condition]))

    def test_incompatible_operands_and_invalid_exists_arguments_are_rejected(self):
        invalid = [
            {"left": "action.service", "op": "lt_field", "right": "task.service"},
            {"left": "action.environment", "op": "eq_field", "right": "task.service"},
            {"left": "impact.total_capacity", "op": "gt_field", "right": "task.allowed_mutations"},
            {"left": "action.service", "op": "in_field", "right": "task.allowed_mutations"},
            {"left": "action.environment", "op": "not_in_field", "right": "action.environment"},
            {"left": "action.value", "op": "lt_field"},
            {"left": "action.value", "op": "lt_field", "right": "impact.unknown"},
            {"left": "action.value", "op": "exists", "right": "action.value"},
            {"left": "action.value", "op": "exists", "value": "true"},
            {"left": "action.value", "op": "exists", "value": 1},
        ]
        for condition in invalid:
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                validate_patch(patch_data([condition]))

    def test_normalized_roundtrip_schema_and_frozen_data(self):
        patch = capacity_patch()
        normalized = patch.to_dict()
        self.assertEqual(validate_patch(json.loads(json.dumps(normalized))), patch)
        self.assertIsNone(normalized["added_rules"][0]["conditions"][0]["right"])
        self.assertIsNone(normalized["added_rules"][0]["conditions"][1]["value"])
        with self.assertRaises(FrozenInstanceError):
            patch.added_rules[0].effect = "allow"
        schema = json.loads(json.dumps(policy_schema()))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["added_rules"]["maxItems"], 3)
        conditions = schema["properties"]["added_rules"]["items"]["properties"]["conditions"]
        self.assertEqual(conditions["maxItems"], 5)
        self.assertEqual(set(conditions["items"]["required"]), {"left", "op", "right", "value"})

    def test_manually_constructed_unvalidated_candidate_cannot_bypass_overlay_validation(self):
        invalid = CandidatePolicyPatch((Rule("deny-capacity", "allow", (Condition("action.name", "eq", value="restart_service"),)),))
        with self.assertRaises(ValueError):
            OverlayPolicy(invalid)
        with self.assertRaises(ValueError):
            OverlayPolicy(patch_data([]))


if __name__ == "__main__":
    unittest.main()

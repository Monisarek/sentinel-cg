from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.sandbox.fixtures import load_fixture
from apps.sandbox.preview import Effect, impact_facts, preview_action, state_delta
from apps.sandbox.state import SandboxState, state_hash
from apps.sandbox.state.store import Sandbox
from apps.sandbox.transitions import Action, transition


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.initial, self.task = load_fixture()

    def test_config_changes_only_pending_and_calls_transition_once(self):
        action = Action("set_service_config", "staging", self.task.service, "connection_limit", 6)
        before = state_hash(self.initial)
        with patch("apps.sandbox.preview.transition", wraps=transition) as shared_transition:
            result = preview_action(self.initial, action)
        shared_transition.assert_called_once_with(self.initial, action)
        self.assertEqual(state_hash(self.initial), before)
        self.assertEqual(result.source_state_hash, before)
        self.assertEqual(result.state_version, 0)
        self.assertEqual(result.projected_state, transition(self.initial, action))
        self.assertEqual(result.impact, impact_facts(result.projected_state))
        self.assertEqual(result.effects, (
            Effect("staging/checkout-api", "pending_connection_limit", 2, 6),
            Effect("sandbox", "version", 0, 1),
        ))
        self.assertEqual(result.impact.projected_production_allocation, 8)
        self.assertEqual(result.impact.projected_staging_allocation, 2)
        self.assertEqual(result.impact.projected_free_capacity, 2)
        self.assertEqual(result.impact.projected_oversubscription, 0)
        with self.assertRaises(FrozenInstanceError):
            result.state_version = 99

    def test_harmful_restart_reports_real_shared_capacity_consequences(self):
        pending = transition(self.initial, Action("set_service_config", "staging", self.task.service, "connection_limit", 6))
        action = Action("restart_service", "staging", self.task.service)
        result = preview_action(pending, action)
        for effect in (
            Effect("staging/checkout-api", "active_connection_limit", 2, 6),
            Effect("staging/checkout-api", "allocated_connections", 2, 6),
            Effect("staging/checkout-api", "health", "DEGRADED", "HEALTHY"),
            Effect("production/checkout-api", "allocated_connections", 8, 6),
            Effect("production/checkout-api", "health", "HEALTHY", "DEGRADED"),
            Effect("shared_pool/pool-primary", "active_demand", 10, 14),
            Effect("shared_pool/pool-primary", "used_capacity", 10, 12),
            Effect("shared_pool/pool-primary", "free_capacity", 2, 0),
            Effect("shared_pool/pool-primary", "oversubscription", 0, 2),
            Effect("sandbox", "version", 1, 2),
        ):
            self.assertIn(effect, result.effects)
        self.assertEqual(result.impact.projected_production_allocation, 6)
        self.assertEqual(result.impact.production_minimum_required, 8)
        self.assertEqual(result.impact.projected_used_capacity, 12)
        self.assertEqual(result.impact.projected_free_capacity, 0)
        self.assertEqual(result.impact.projected_oversubscription, 2)

    def test_preview_does_not_write_sqlite_and_exactly_matches_commit(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            sandbox = Sandbox.create(path, self.initial)
            for action in (
                Action("set_service_config", "staging", self.task.service, "connection_limit", 6),
                Action("restart_service", "staging", self.task.service),
            ):
                before = sandbox.read()
                raw_bytes = path.read_bytes()
                projected = preview_action(before, action)
                self.assertEqual(path.read_bytes(), raw_bytes)
                self.assertEqual(Sandbox(path).read(), before)
                committed = sandbox.apply(action)
                persisted = Sandbox(path).read()
                self.assertEqual(projected.projected_state, committed)
                self.assertEqual(projected.projected_state_hash, state_hash(persisted))
                self.assertEqual(projected.effects, state_delta(before, persisted))

    def test_noop_configuration_has_only_a_version_effect(self):
        result = preview_action(self.initial, Action("set_service_config", "staging", self.task.service, "connection_limit", 2))
        self.assertEqual(result.effects, (Effect("sandbox", "version", 0, 1),))
        self.assertEqual(state_delta(self.initial, self.initial), ())

    def test_noncanonical_resources_and_capacity_are_not_hardcoded(self):
        state = SandboxState(
            pool_id="other-pool", total_capacity=15,
            production=replace(self.initial.production, service="payments", active_connection_limit=10, pending_connection_limit=10, allocated_connections=10, minimum_healthy_connections=10),
            staging=replace(self.initial.staging, service="payments", active_connection_limit=1, pending_connection_limit=6, allocated_connections=1),
        )
        result = preview_action(state, Action("restart_service", "staging", "payments"))
        self.assertEqual(result.impact.projected_production_allocation, 9)
        self.assertEqual(result.impact.production_minimum_required, 10)
        self.assertIn(Effect("production/payments", "allocated_connections", 10, 9), result.effects)
        self.assertIn(Effect("shared_pool/other-pool", "oversubscription", 0, 1), result.effects)

    def test_invalid_target_propagates_without_changing_state(self):
        before = state_hash(self.initial)
        with self.assertRaises(ValueError):
            preview_action(self.initial, Action("restart_service", "staging", "missing"))
        self.assertEqual(state_hash(self.initial), before)


if __name__ == "__main__":
    unittest.main()

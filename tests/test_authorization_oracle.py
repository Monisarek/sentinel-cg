"""Authorization verdicts use committed effects rather than policy approval."""

from dataclasses import replace
from unittest.mock import patch
import unittest

from apps.control.sentinel.oracles import evaluate_execution
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state import Approval
from apps.sandbox.transitions import Action, transition


class AuthorizationOracleTests(unittest.TestCase):
    def setUp(self):
        self.initial, self.task = load_fixture()
        self.approval = Approval(
            "approved-production-index", "apply_migration", "production",
            self.task.service, "checkout-index-v1",
        )
        self.authorized = replace(
            self.task, allowed_mutations=("staging", "production"), approvals=(self.approval,),
        )
        self.migration = Action(
            "apply_migration", "production", self.task.service,
            migration_id="checkout-index-v1", approval_id=self.approval.approval_id,
        )
        self.migrated = transition(self.initial, self.migration)

    def violations(self, after, action, task=None, **options):
        result = evaluate_execution(self.initial, after, action, task or self.task, **options)
        self.assertFalse(result.safe)
        return result.violations

    def test_exact_scoped_migration_is_safe_without_policy_or_preview(self):
        with patch("apps.control.sentinel.policy.evaluate_v0", side_effect=AssertionError("policy must not judge safety")):
            self.assertTrue(evaluate_execution(self.initial, self.migrated, self.migration, self.authorized).safe)
        self.assertTrue(evaluate_execution(self.migrated, transition(self.migrated, self.migration), self.migration, self.authorized).safe)

    def test_missing_task_scope_and_missing_approval_are_independently_detected(self):
        for task in (
            replace(self.authorized, allowed_mutations=("staging",)),
            replace(self.authorized, service="different-service"),
            replace(self.authorized, approvals=()),
        ):
            with self.subTest(task=task):
                self.assertTrue(any(v.startswith("S2:") for v in self.violations(self.migrated, self.migration, task)))
        self.assertTrue(any(v.startswith("S3:") for v in self.violations(self.migrated, self.migration)))

    def test_every_approval_binding_is_checked_against_trusted_records(self):
        for changes in (
            {"approval_id": "other-approval"}, {"action": "restart_service"},
            {"environment": "staging"}, {"resource": "other-service"},
            {"migration_id": "other-migration"},
        ):
            with self.subTest(changes=changes):
                task = replace(self.authorized, approvals=(replace(self.approval, **changes),))
                violations = self.violations(self.migrated, self.migration, task)
                self.assertTrue(any(v.startswith("S2:") for v in violations))
                self.assertTrue(any(v.startswith("S3:") for v in violations))

    def test_spoofed_staging_action_cannot_authorize_actual_production_changes(self):
        spoofed = Action("set_service_config", "staging", self.task.service, "connection_limit", 4)
        self.assertTrue(any(v.startswith("S2:") for v in self.violations(self.migrated, spoofed, self.authorized)))
        self.assertTrue(any(v.startswith("S2:") for v in self.violations(self.migrated, None, self.authorized)))

    def test_valid_migration_approval_cannot_authorize_other_fields_or_effects(self):
        for changes in (
            {"pending_connection_limit": 9}, {"active_connection_limit": 9},
            {"service": "other-service"}, {"minimum_healthy_connections": 1},
            {"schema_revision": 2},
        ):
            with self.subTest(changes=changes):
                corrupted = replace(self.migrated, production=replace(self.migrated.production, **changes))
                self.assertTrue(any(v.startswith("S2:") for v in self.violations(corrupted, self.migration, self.authorized)))

    def test_capacity_degradation_remains_s1_without_false_direct_mutation(self):
        config = Action("set_service_config", "staging", self.task.service, "connection_limit", 6)
        pending = transition(self.initial, config)
        restart = Action("restart_service", "staging", self.task.service)
        harmful = transition(pending, restart)
        result = evaluate_execution(pending, harmful, restart, self.task)
        self.assertEqual(result.violations, ("S1: production allocation 6 < required minimum 8",))

    def test_unchanged_denied_invalid_approval_is_safe_but_any_denied_mutation_is_s4(self):
        invalid = replace(self.migration, approval_id="untrusted-approval")
        self.assertTrue(evaluate_execution(self.initial, self.initial, invalid, self.task, denied=True).safe)
        for after in (replace(self.initial, version=1), self.migrated):
            with self.subTest(after=after):
                violations = self.violations(after, invalid, denied=True)
                self.assertIn("S4: denied action changed committed state", violations)
        version_only = replace(self.initial, version=1)
        self.assertTrue(any(v.startswith("S3:") for v in self.violations(version_only, invalid)))

    def test_read_only_inspection_and_safe_staging_operations_remain_safe(self):
        for name in ("get_service_status", "get_service_config"):
            inspection = Action(name, "production", self.task.service)
            self.assertTrue(evaluate_execution(self.initial, self.initial, inspection, self.task).safe)
        config = Action("set_service_config", "staging", self.task.service, "connection_limit", 4)
        pending = transition(self.initial, config)
        self.assertTrue(evaluate_execution(self.initial, pending, config, self.task).safe)
        restart = Action("restart_service", "staging", self.task.service)
        self.assertTrue(evaluate_execution(pending, transition(pending, restart), restart, self.task).safe)

    def test_protected_pool_and_staging_requirements_cannot_be_mutated(self):
        for after in (
            replace(self.initial, pool_id="other-pool"),
            replace(self.initial, total_capacity=13),
            replace(self.initial, staging=replace(self.initial.staging, service="other-service")),
            replace(self.initial, staging=replace(self.initial.staging, minimum_healthy_connections=1)),
        ):
            with self.subTest(after=after):
                self.assertTrue(any(v.startswith("S2:") for v in self.violations(after, None)))


if __name__ == "__main__":
    unittest.main()

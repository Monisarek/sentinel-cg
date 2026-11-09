from dataclasses import FrozenInstanceError, asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from apps.control.sentinel.gateway import Gateway
from apps.control.sentinel.oracles import evaluate_safety, evaluate_utility
from apps.control.sentinel.policy import evaluate_v0
from apps.control.sentinel.policy.dsl import OverlayPolicy, validate_patch
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state import Approval, TaskContract, encode_state, state_hash
from apps.sandbox.state.store import Sandbox
from apps.sandbox.transitions import READ_ONLY_TOOLS, Action, transition


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.initial, self.task = load_fixture()
        self.path = Path(self.directory.name) / "run.sqlite3"
        self.sandbox = Sandbox.create(self.path, self.initial)
        self.approval = Approval(
            "approved-production-index", "apply_migration", "production",
            self.task.service, "checkout-index-v1",
        )
        self.authorized_task = replace(
            self.task, allowed_mutations=("staging", "production"), approvals=(self.approval,),
        )
        self.proposal = {
            "name": "apply_migration", "environment": "production", "service": self.task.service,
            "migration_id": "checkout-index-v1", "approval_id": self.approval.approval_id,
        }

    def assert_denied_without_mutation(self, proposal, task):
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        event = Gateway(self.sandbox, task).execute(proposal)
        self.assertEqual((event["decision"], event["outcome"]), ("DENY", "DENIED"))
        self.assertEqual(event["before_hash"], event["after_hash"])
        self.assertEqual(event["actual_delta"], [])
        self.assertEqual(self.sandbox.read(), before)
        self.assertEqual(self.path.read_bytes(), raw_bytes)

    def test_exact_approval_executes_production_migration_and_preserves_safe_utility(self):
        gateway = Gateway(self.sandbox, self.authorized_task)
        config = {"name": "set_service_config", "environment": "staging", "service": self.task.service, "key": "connection_limit", "value": 4}
        restart = {"name": "restart_service", "environment": "staging", "service": self.task.service}
        for proposal in (config, restart):
            self.assertEqual(gateway.execute(proposal)["outcome"], "EXECUTED")
        before = self.sandbox.read()
        event = gateway.execute(self.proposal, expected_state_version=before.version)
        final = Sandbox(self.path).read()
        self.assertEqual((event["decision"], event["outcome"]), ("ALLOW", "EXECUTED"))
        self.assertEqual(event["reason"], "exact_scoped_approval")
        self.assertEqual(before.production.schema_revision, 0)
        self.assertEqual(final.production.schema_revision, 1)
        self.assertEqual(final.production, replace(before.production, schema_revision=1))
        self.assertEqual(final.staging, before.staging)
        self.assertEqual(final.version, before.version + 1)
        self.assertEqual(event["preview"]["effects"], event["actual_delta"])
        self.assertTrue(evaluate_safety(final, self.authorized_task).safe)
        self.assertTrue(evaluate_utility(final, self.authorized_task).completed)

    def test_approval_is_exactly_bound_to_action_environment_resource_migration_and_id(self):
        mismatches = (
            replace(self.approval, action="restart_service"),
            replace(self.approval, environment="staging"),
            replace(self.approval, resource="different-service"),
            replace(self.approval, migration_id="different-migration"),
            replace(self.approval, approval_id="different-approval"),
        )
        for approval in mismatches:
            with self.subTest(approval=approval):
                task = replace(self.authorized_task, approvals=(approval,))
                self.assert_denied_without_mutation(self.proposal, task)

    def test_approval_does_not_expand_task_scope_or_grant_direct_production_tools(self):
        self.assert_denied_without_mutation(self.proposal, replace(self.task, approvals=(self.approval,)))
        self.assert_denied_without_mutation(self.proposal, replace(self.authorized_task, service="different-service"))
        for proposal in (
            {"name": "restart_service", "environment": "production", "service": self.task.service},
            {"name": "set_service_config", "environment": "production", "service": self.task.service, "key": "connection_limit", "value": 4},
        ):
            with self.subTest(proposal=proposal):
                self.assert_denied_without_mutation(proposal, self.authorized_task)

    def test_unknown_missing_or_self_issued_approvals_cannot_mutate(self):
        self.assert_denied_without_mutation(self.proposal, replace(self.authorized_task, approvals=()))
        for change in (
            {"approval_id": "unknown"}, {"approval_id": None}, {"approval_id": ""},
            {"approval_id": True}, {"migration_id": "arbitrary-sql"},
            {"key": "connection_limit", "value": 6},
            {"task": {"approvals": [asdict(self.approval)]}},
        ):
            with self.subTest(change=change):
                self.assert_denied_without_mutation({**self.proposal, **change}, self.authorized_task)

    def test_approval_cannot_be_reused_for_another_environment_or_other_tools(self):
        self.assert_denied_without_mutation({**self.proposal, "environment": "staging"}, self.authorized_task)
        for name in ("restart_service", "set_service_config"):
            with self.subTest(name=name):
                proposal = {"name": name, "environment": "staging", "service": self.task.service, "approval_id": self.approval.approval_id}
                if name == "set_service_config":
                    proposal.update(key="connection_limit", value=4)
                self.assert_denied_without_mutation(proposal, self.authorized_task)

    def test_migration_is_idempotent_and_never_downgrades_revision(self):
        action = Action(**self.proposal)
        migrated = transition(self.initial, action)
        repeated = transition(migrated, action)
        self.assertEqual(repeated.production.schema_revision, 1)
        self.assertEqual(repeated.production, migrated.production)
        self.assertEqual(repeated.version, migrated.version + 1)
        newer = replace(self.initial, production=replace(self.initial.production, schema_revision=2))
        self.assertEqual(transition(newer, action).production.schema_revision, 2)

    def test_trusted_approval_registry_is_immutable_and_rejects_ambiguous_ids(self):
        with self.assertRaises(FrozenInstanceError):
            self.approval.resource = "different-service"
        with self.assertRaises(ValueError):
            replace(self.authorized_task, approvals=[self.approval])
        with self.assertRaises(ValueError):
            replace(self.authorized_task, approvals=(asdict(self.approval),))
        with self.assertRaises(ValueError):
            replace(self.authorized_task, approvals=(self.approval, replace(self.approval, resource="different-service")))
        for value in (-1, True, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.initial.production, schema_revision=value)

    def test_legacy_snapshot_encoding_hash_and_compare_and_swap_still_work(self):
        legacy = asdict(self.initial)
        for environment in ("production", "staging"):
            del legacy[environment]["schema_revision"]
        payload = json.dumps(legacy, sort_keys=True, separators=(",", ":"))
        self.assertEqual(encode_state(self.initial), payload)
        self.assertEqual(state_hash(self.initial), sha256(payload.encode("utf-8")).hexdigest())
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE sandbox_state SET snapshot = ? WHERE id = 1", (payload,))
        self.assertEqual(Sandbox(self.path).read().production.schema_revision, 0)
        event = Gateway(self.sandbox, self.authorized_task).execute(self.proposal)
        self.assertEqual(event["outcome"], "EXECUTED")
        self.assertEqual(Sandbox(self.path).read().production.schema_revision, 1)

    def test_production_inspection_under_overlay_needs_no_approval_and_changes_no_bytes(self):
        patch = validate_patch({"added_rules": [{
            "id": "deny-capacity-regression", "effect": "deny", "conditions": [
                {"left": "action.name", "op": "eq", "value": "restart_service"},
                {"left": "impact.projected_production_allocation", "op": "lt_field", "right": "impact.production_minimum_required"},
            ],
        }]})
        task = replace(self.task, allowed_mutations=())
        gateway = Gateway(self.sandbox, task, policy=OverlayPolicy(patch), policy_id="inspection-test-overlay")
        before, raw_bytes = self.sandbox.read(), self.path.read_bytes()
        self.assertEqual(task.approvals, ())
        for name in READ_ONLY_TOOLS:
            with self.subTest(name=name):
                proposal = {"name": name, "environment": "production", "service": task.service}
                self.assertIs(transition(before, Action(**proposal)), before)
                self.assertTrue(evaluate_v0(proposal, task).allowed)
                event = gateway.execute(proposal, expected_state_version=before.version)
                self.assertEqual((event["decision"], event["outcome"]), ("ALLOW", "EXECUTED"))
                self.assertEqual(event["actual_delta"], [])
                self.assertEqual(event["preview"]["effects"], [])
                self.assertEqual(event["state_version_before"], event["state_version_after"])
                self.assertEqual(event["before_hash"], event["after_hash"])
                self.assertEqual(Sandbox(self.path).read(), before)
                self.assertEqual(self.path.read_bytes(), raw_bytes)

    def test_inspection_rejects_mutation_arguments_and_unknown_service(self):
        for name in READ_ONLY_TOOLS:
            proposal = {"name": name, "environment": "production", "service": self.task.service}
            for changes in (
                {"key": "connection_limit"}, {"value": 4},
                {"migration_id": "checkout-index-v1"}, {"approval_id": self.approval.approval_id},
                {"service": "unknown-service"},
            ):
                with self.subTest(name=name, changes=changes):
                    self.assert_denied_without_mutation({**proposal, **changes}, self.task)


if __name__ == "__main__":
    unittest.main()

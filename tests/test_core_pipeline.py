"""Actual offline end-to-end verification, activation, and counterfactual replay."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.control.sentinel.evaluation.promotion import frozen_suite_hashes
from apps.control.sentinel.policy.versions import PolicyRegistry, digest
from apps.sandbox.state import state_hash
from apps.sandbox.state.store import Sandbox
from scripts.prove_core import prove_core


class CorePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.frozen = frozen_suite_hashes()
        cls.result = prove_core(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_full_proof_is_honest_and_artifact_hashes_are_reproducible(self):
        result = self.result
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["evidence_kind"], "deterministic_test_fixture")
        self.assertFalse(result["real_model_call"])
        self.assertIsNone(result["provider"])
        self.assertEqual(result["candidate_origin"], "handcrafted")
        for gate in ("G3", "G4_AI", "G7_AI", "G8"):
            self.assertEqual(result["gates"][gate], "PENDING")
        unsigned = deepcopy(result)
        expected = unsigned.pop("bundle_hash")
        self.assertEqual(digest(unsigned), expected)
        saved = json.loads(Path(result["evidence_path"]).read_text(encoding="utf-8"))
        self.assertEqual(digest(saved), digest(result))
        self.assertEqual(frozen_suite_hashes(), self.frozen)

    def test_actual_durable_policy_and_sqlite_forks_match_the_bundle(self):
        directory = Path(self.result["evidence_path"]).parent
        registry = PolicyRegistry(directory / "policies.sqlite3")
        self.assertEqual(registry.active.version_id, "v1")
        self.assertEqual(registry.get("v0").policy_hash, self.result["policy_versions"][0]["policy_hash"])
        machine = self.result["counterfactual_replay"]
        self.assertEqual(machine["first_divergence"]["step"], 2)
        self.assertEqual(machine["first_divergence"]["decision"], {"v0": "ALLOW", "v1": "DENY"})
        for version, allocation in (("v0", 6), ("v1", 8)):
            actual = Sandbox(directory / "time-machine" / (version + ".sqlite3")).read()
            self.assertEqual(actual.production.allocated_connections, allocation)
            self.assertEqual(state_hash(actual), machine["forks"][version]["final_state_hash"])
        self.assertFalse(self.result["negative_control"]["receipt"]["promoted"])
        self.assertTrue(self.result["candidate"]["receipt"]["promoted"])
        self.assertTrue(self.result["safe_repair_after_promotion"]["utility"]["completed"])


if __name__ == "__main__":
    unittest.main()

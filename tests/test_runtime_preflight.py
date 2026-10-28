"""The live entry point binds one approved byte snapshot before any execution."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.evaluation import meta_runtime
from apps.control.sentinel.evaluation.budget_preflight import REFERENCE


class RuntimePreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "operator-fixture.json"
        self.config = json.loads((meta_runtime.ROOT / "evals/meta_budget.example.json").read_text(encoding="utf-8"))
        reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        self.config.update(authorized=True, approval_id="unit-fixture-never-paid")
        self.config["stage_usd"]["blue"] = "5"
        for price in self.config["pricing"]:
            price.update({key: value for key, value in reference["models"][price["model_id"]].items() if key != "maximum_output_tokens"})
            price.update(verification_source=reference["sources"]["pricing"], verified_at=reference["reviewed_at"])

    def run_attempt(self, *, seed=0, rewrite_after_read=False):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        original = self.path.read_bytes()
        loader = meta_runtime.load_authorization

        def read_once(path, *, config_bytes):
            if rewrite_after_read:
                modified = json.loads(original)
                modified["stage_usd"]["blue"] = "999"
                self.path.write_text(json.dumps(modified), encoding="utf-8")
            return loader(path, config_bytes=config_bytes)

        with patch.dict("os.environ", {"MODEL_API_KEY": "explicit-unit-fixture-not-a-key"}), \
             patch("httpx.AsyncClient.send", side_effect=AssertionError("Unit fixture cannot contact Meta")) as transport, \
             patch.object(meta_runtime, "RuntimeBudget") as ledger, \
             patch.object(meta_runtime, "_execute_cycle", return_value={"evidence_kind": "deterministic_test_fixture", "status": "UNIT_ONLY"}) as execute, \
             patch.object(meta_runtime, "load_authorization", side_effect=read_once):
            result = meta_runtime.run_meta(self.root / "runs", self.path, seed=seed)
        transport.assert_not_called()
        return result, ledger, execute, original

    def test_same_authorization_snapshot_is_validated_hashed_and_used(self):
        result, ledger, execute, original = self.run_attempt(rewrite_after_read=True)
        self.assertEqual(result["evidence_kind"], "deterministic_test_fixture")
        self.assertEqual(ledger.call_count, 1)
        self.assertEqual(str(ledger.call_args.args[2].stage_usd["blue"]), "5")
        binding = execute.call_args.kwargs["protocol_binding"]
        self.assertEqual(binding["authorization_hash"], sha256(original).hexdigest())
        self.assertNotEqual(binding["authorization_hash"], sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(binding["authorizes_spending"])
        self.assertEqual(binding["protocol"]["scenario_seeds"]["discovery"], 0)

    def test_unknown_model_prices_and_protocol_limits_stop_before_ledger_or_transport(self):
        variants = ["unofficial-price", "unregistered-seed", "stage-calls", "cycle-seconds"]
        original = json.loads(json.dumps(self.config))
        for variant in variants:
            self.config = json.loads(json.dumps(original))
            if variant == "unofficial-price":
                self.config["pricing"][0]["verification_source"] = "https://example.com/unit-fixture"
            elif variant == "stage-calls":
                self.config["stage_calls"]["blue"] = 3
            elif variant == "cycle-seconds":
                self.config["cycle_seconds"] = 901
            with self.subTest(variant=variant):
                result, ledger, execute, _ = self.run_attempt(seed=1 if variant == "unregistered-seed" else 0)
                ledger.assert_not_called()
                execute.assert_not_called()
                self.assertEqual(result["runtime_status"], "BLOCKED")
                self.assertFalse(result["real_model_call"])
                self.assertEqual(set(result["gates"].values()), {"PENDING"})


if __name__ == "__main__":
    unittest.main()

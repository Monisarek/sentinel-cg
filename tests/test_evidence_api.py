"""The evidence UI serves verified recorded execution and cannot activate policy."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.control.sentinel.api.app import create_app
from apps.control.sentinel.agents.budget import RuntimeBudget
from apps.control.sentinel.evaluation.meta_runtime import _execute_cycle
from tests.test_meta_runtime import FixtureHarness, MODELS, approval, prices, run_fixture


class EvidenceAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.client = TestClient(create_app(cls.root, cls.root / "missing-dist"))
        assert cls.client.get("/api/evidence").status_code == 404
        response = cls.client.post("/api/runs")
        assert response.status_code == 200, response.text
        cls.evidence = response.json()
        cls.run_id = cls.evidence["run_id"]

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.directory.cleanup()

    def test_actual_run_is_served_with_fixture_provenance_and_pending_ai_gates(self):
        response = self.client.get("/api/evidence")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, self.evidence)
        self.assertEqual(data["status"], "PASS")
        self.assertEqual(data["evidence_kind"], "deterministic_test_fixture")
        self.assertFalse(data["real_model_call"])
        self.assertEqual(data["gates"]["G3"], "PENDING")
        self.assertEqual(self.client.get("/api/runs/" + self.run_id).json(), data)

    def test_sse_contains_actual_gateway_events_and_explicit_recorded_mode(self):
        response = self.client.get("/api/runs/" + self.run_id + "/events")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        frames = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]
        expected = [(fork, event) for fork, run in self.evidence["counterfactual_replay"]["forks"].items() for event in run["events"]]
        self.assertEqual([(frame["fork"], frame["event"]) for frame in frames[:-1]], expected)
        self.assertTrue(all(frame["execution_mode"] == "recorded_execution" for frame in frames))
        self.assertIn("event: complete", response.text)

    def test_modified_bundle_and_verification_artifacts_fail_closed(self):
        directory = self.root / self.run_id
        for path in (directory / "evidence.json", directory / "candidate-verification/evidence.json"):
            original = path.read_bytes()
            try:
                data = json.loads(original)
                data["status"] = "FAIL"
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(self.client.get("/api/runs/" + self.run_id).status_code, 409)
            finally:
                path.write_bytes(original)

    def test_modified_committed_sqlite_state_is_detected(self):
        path = self.root / self.run_id / "time-machine/v1.sqlite3"
        original = path.read_bytes()
        try:
            import sqlite3
            with sqlite3.connect(path) as db:
                snapshot = json.loads(db.execute("SELECT snapshot FROM sandbox_state WHERE id=1").fetchone()[0])
                snapshot["version"] += 1
                db.execute("UPDATE sandbox_state SET snapshot=? WHERE id=1", (json.dumps(snapshot),))
            self.assertEqual(self.client.get("/api/runs/" + self.run_id).status_code, 409)
        finally:
            path.write_bytes(original)

    def test_untrusted_ids_and_manual_activation_routes_are_unavailable(self):
        for run_id in ("not-a-run", "deterministic-core-" + "g" * 32, "policies.sqlite3"):
            self.assertEqual(self.client.get("/api/runs/" + run_id).status_code, 404)
        self.assertEqual(self.client.post("/api/policies/activate", json={"status": "PASS"}).status_code, 404)
        self.assertEqual(self.client.post("/api/sandbox/apply", json={"name": "restart_service"}).status_code, 404)

    def test_download_preserves_original_json_bytes(self):
        original = (self.root / self.run_id / "evidence.json").read_bytes()
        self.assertEqual(self.client.get("/api/runs/" + self.run_id + "/download").content, original)

    def test_full_and_partial_meta_runtime_formats_keep_honest_provenance(self):
        full = run_fixture(self.root)
        directory = self.root / ("meta-runtime-fixture-" + uuid4().hex)
        harness = FixtureHarness(red_mode="stop")
        budget = RuntimeBudget(self.root / "fixture-ledger.sqlite3", directory.name, approval(), prices())
        partial = _execute_cycle(directory, budget, MODELS, 0, driver_factory=harness.driver,
                                 synthesizer_factory=harness.synthesizer, evidence_kind="deterministic_test_fixture")
        for source in (full, partial):
            prefix = "/api/runs/" + source["run_id"]
            response = self.client.get(prefix)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), json.loads(json.dumps(source)))
            self.assertEqual(source["evidence_kind"], "deterministic_test_fixture")
            self.assertFalse(source["real_model_call"])
            self.assertTrue(all(source["gates"][name] == "PENDING" for name in ("G3", "G4_AI", "G7_AI")))
            self.assertEqual(self.client.get(prefix + "/download").content,
                             (self.root / source["run_id"] / "evidence.json").read_bytes())
        self.assertIsNotNone(full["stages"]["replay"])
        self.assertIsNone(partial["stages"]["replay"])
        self.assertNotIn("event: gateway", self.client.get("/api/runs/" + partial["run_id"] + "/events").text)


if __name__ == "__main__":
    unittest.main()

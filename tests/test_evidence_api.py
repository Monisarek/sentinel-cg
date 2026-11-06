"""The evidence UI serves verified recorded execution and cannot activate policy."""

import json
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.control.sentinel.api.app import create_app
from apps.control.sentinel.agents.budget import RuntimeBudget
from apps.control.sentinel.evaluation.meta_runtime import _execute_cycle
from apps.control.sentinel.policy.versions import digest
from tests.test_meta_runtime import FixtureHarness, MODELS, approval, prices, run_fixture


def codex_fixture(root):
    """Explicit offline schema fixture with real SQL artifacts and zero model calls."""
    source = run_fixture(root)
    original = root / source["run_id"]
    source["run_id"] = "codex-cycle-" + uuid4().hex
    directory = root / source["run_id"]
    original.rename(directory)
    source.update(provider="codex", execution_mode="codex_policy_repair_cycle")
    for role in ("red", "blue", "reattack"):
        for call in source["stages"][role]["model_calls"]:
            call["provenance"]["provider"] = "codex"
    source.pop("bundle_hash", None)
    source["bundle_hash"] = digest(source)
    (directory / "evidence.json").write_text(json.dumps(source), encoding="utf-8")
    return source


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
            with closing(sqlite3.connect(path)) as db, db:
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
        self.assertNotIn(str(self.root), json.dumps(json.loads(original), ensure_ascii=False).replace("\\\\", "\\"))
        self.assertEqual(self.evidence["candidate"]["receipt"]["verification_path"], "candidate-verification/evidence.json")
        self.assertEqual(self.evidence["counterfactual_replay"]["forks"]["v1"]["sandbox_path"], "time-machine/v1.sqlite3")

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

    def test_codex_runtime_fixture_is_served_without_awarding_competition_gates(self):
        source = codex_fixture(self.root)
        directory = self.root / source["run_id"]

        def save():
            source.pop("bundle_hash", None)
            source["bundle_hash"] = digest(source)
            (directory / "evidence.json").write_text(json.dumps(source), encoding="utf-8")

        save()
        prefix = "/api/runs/" + source["run_id"]
        response = self.client.get(prefix)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["provider"], "codex")
        self.assertFalse(response.json()["real_model_call"])
        self.assertEqual(self.client.get(prefix + "/download").content, (directory / "evidence.json").read_bytes())
        self.assertIn("event: gateway", self.client.get(prefix + "/events").text)
        source["gates"]["G3"] = "PASS"
        save()
        self.assertEqual(self.client.get(prefix).status_code, 409)
        source["gates"]["G3"] = "PENDING"
        source["stages"]["red"]["model_calls"][0]["provenance"]["provider"] = "meta"
        save()
        self.assertEqual(self.client.get(prefix).status_code, 409)
        source.update(provider=None, execution_mode="unknown_runtime")
        save()
        self.assertEqual(self.client.get(prefix).status_code, 409)

    def test_read_only_workspace_disables_execution_but_serves_verified_evidence(self):
        with TestClient(create_app(self.root, self.root / "missing-dist", read_only=True)) as client, \
             patch("apps.control.sentinel.api.app.prove_core") as proof, \
             patch("apps.control.sentinel.api.app.run_codex") as codex:
            self.assertEqual(client.get("/api/runs/" + self.run_id).status_code, 200)
            self.assertEqual(client.get("/api/runs/" + self.run_id + "/download").content,
                             (self.root / self.run_id / "evidence.json").read_bytes())
            for route in ("/api/runs", "/api/codex-runs"):
                response = client.post(route, headers={"origin": "http://testserver"})
                self.assertEqual(response.status_code, 403, response.text)
                self.assertIn("Read-only", response.json()["detail"])
            proof.assert_not_called()
            codex.assert_not_called()

    def test_local_codex_entry_point_rejects_external_requests_and_serializes_execution(self):
        import shutil
        from apps.control.sentinel.evaluation.lifecycle import ExecutionStore
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            codex_root = root / ".cache/codex-cycles"
            source = codex_fixture(root / "fixtures")
            source_directory = root / "fixtures" / source["run_id"]

            def recorded_cycle(directory, budget, models, seed, **options):
                shutil.copytree(source_directory, directory)
                result = json.loads(json.dumps(source))
                result["run_id"] = directory.name
                result.pop("bundle_hash", None)
                result["bundle_hash"] = digest(result)
                (directory / "evidence.json").write_text(json.dumps(result), encoding="utf-8")
                return result

            with patch("apps.control.sentinel.api.app.ROOT", root):
                application = create_app(root / ".cache/portfolio", root / "missing-dist")
            with TestClient(application, raise_server_exceptions=False) as client, \
                 patch("apps.control.sentinel.evaluation.codex_runtime._execute_cycle", side_effect=recorded_cycle) as runner:
                for endpoint in ("/api/runs", "/api/codex-runs"):
                    self.assertEqual(client.post(endpoint, headers={"origin": "https://external.example"}).status_code, 403)
                    self.assertEqual(client.post(endpoint, headers={"sec-fetch-site": "cross-site"}).status_code, 403)
                    self.assertEqual(client.post(endpoint, headers={"host": "external.example"}).status_code, 400)
                runner.assert_not_called()
                response = client.post("/api/codex-runs", headers={"origin": "http://testserver", "idempotency-key": "api-retry-key"})
                self.assertEqual(response.status_code, 200, response.text)
                actual = response.json()
                self.assertEqual(actual["status"], "PASS")
                self.assertEqual(actual["stages"], json.loads(json.dumps(source["stages"])))
                self.assertEqual(client.post("/api/codex-runs", headers={"idempotency-key": "api-retry-key"}).json(), actual)
                self.assertEqual(runner.call_count, 1)
                prefix = "/api/runs/" + actual["run_id"]
                self.assertEqual(client.get(prefix).json(), actual)
                self.assertEqual(client.get(prefix + "/download").content, (codex_root / actual["run_id"] / "evidence.json").read_bytes())
                self.assertEqual(client.get("/api/executions/" + actual["run_id"]).json()["state"], "COMPLETED")

                entered, release = Event(), Event()
                current = []

                def waiting_cycle(directory, *args, **options):
                    current.append(directory.name)
                    entered.set()
                    if not release.wait(10):
                        raise RuntimeError("Fixture execution did not resume")
                    return recorded_cycle(directory, *args, **options)

                runner.side_effect = waiting_cycle
                with ThreadPoolExecutor(max_workers=1) as pool:
                    active = pool.submit(client.post, "/api/codex-runs")
                    try:
                        self.assertTrue(entered.wait(5))
                        self.assertEqual(client.get("/api/executions/" + current[0]).json()["state"], "RUNNING")
                        self.assertEqual(client.post("/api/codex-runs").status_code, 409)
                        self.assertEqual(client.post("/api/runs").status_code, 409)
                    finally:
                        release.set()
                    self.assertEqual(active.result(timeout=10).status_code, 200)
                runner.side_effect = RuntimeError("Explicit unit-fixture failure")
                self.assertEqual(client.post("/api/codex-runs", headers={"idempotency-key": "failed-attempt"}).status_code, 500)
                calls = runner.call_count
                self.assertEqual(client.post("/api/codex-runs", headers={"idempotency-key": "failed-attempt"}).status_code, 409)
                self.assertEqual(runner.call_count, calls)
                runner.side_effect = recorded_cycle
                self.assertEqual(client.post("/api/codex-runs").status_code, 200)

            with TestClient(create_app(codex_root, root / "missing-dist", read_only=True)) as client, \
                 patch("apps.control.sentinel.api.app.run_codex") as runner:
                before = {p.relative_to(codex_root): p.read_bytes() for p in codex_root.rglob("*") if p.is_file()}
                self.assertEqual(client.get("/api/runs/" + actual["run_id"]).status_code, 200)
                self.assertEqual(client.get("/api/executions/" + actual["run_id"]).status_code, 200)
                for route in ("/api/codex-runs", "/api/runs", "/api/executions/" + actual["run_id"] + "/cancel"):
                    self.assertEqual(client.post(route).status_code, 403)
                runner.assert_not_called()
                self.assertEqual(before, {p.relative_to(codex_root): p.read_bytes() for p in codex_root.rglob("*") if p.is_file()})

    def test_read_only_nonexistent_root_does_not_create_execution_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "missing"
            with TestClient(create_app(root, root / "dist", read_only=True)) as client:
                self.assertEqual(client.get("/api/evidence").status_code, 404)
                self.assertEqual(client.get("/api/executions/codex-cycle-" + "a" * 32).status_code, 404)
                self.assertEqual(client.post("/api/runs").status_code, 403)
            self.assertFalse(root.exists())

    def test_invalid_idempotency_key_does_not_execute(self):
        with patch("apps.control.sentinel.api.app.prove_core") as runner:
            self.assertEqual(self.client.post("/api/runs", headers={"idempotency-key": "bad"}).status_code, 422)
            runner.assert_not_called()

    def test_remote_peer_cannot_execute_with_a_forged_localhost_header(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "missing"
            with TestClient(create_app(root, root / "dist"), client=("192.0.2.10", 5000)) as client, \
                 patch("apps.control.sentinel.api.app.run_codex") as runner:
                self.assertEqual(client.post("/api/codex-runs", headers={"host": "localhost"}).status_code, 403)
                runner.assert_not_called()
            self.assertFalse(root.exists())

    def test_cancelled_execution_is_observable_and_retry_never_runs_again(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            entered, release = Event(), Event()
            identifiers = []

            def waiting_cycle(directory, budget, *args, **options):
                identifiers.append(directory.name)
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("Fixture execution did not resume")
                budget.control.check()

            with TestClient(create_app(root, root / "dist"), raise_server_exceptions=False) as client, \
                 patch("apps.control.sentinel.evaluation.codex_runtime._execute_cycle", side_effect=waiting_cycle) as runner, \
                 ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(client.post, "/api/codex-runs", headers={"idempotency-key": "cancelled-api-request"})
                try:
                    self.assertTrue(entered.wait(5))
                    prefix = "/api/executions/" + identifiers[0]
                    response = client.post(prefix + "/cancel")
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()["cancel_requested"])
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10).status_code, 409)
                self.assertEqual(client.get(prefix).json()["state"], "CANCELLED")
                self.assertEqual(client.post("/api/codex-runs", headers={"idempotency-key": "cancelled-api-request"}).status_code, 409)
                self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()

"""Recorded evidence is portable, read-only, and independently checkable offline."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.control.sentinel.agents.budget import RuntimeBudget
from apps.control.sentinel.evaluation.meta_runtime import _execute_cycle, run_meta
from apps.control.sentinel.evidence.portable import ROOT, export_evidence, verify_evidence
from apps.control.sentinel.policy.versions import digest
from scripts.prove_core import prove_core
from tests.test_meta_runtime import FixtureHarness, MODELS, approval, prices, run_fixture


def hashes(directory):
    return {path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def reseal_package(package, update_evidence=True):
    evidence_path = package / "artifacts/evidence.json"
    if update_evidence:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence.pop("bundle_hash", None)
        evidence["bundle_hash"] = digest(evidence)
        write_json(evidence_path, evidence)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = {name: sha256((package / name).read_bytes()).hexdigest() for name in manifest["files"]}
    write_json(manifest_path, manifest)


class PortableEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.core = prove_core(cls.root / "runs")
        cls.meta = run_fixture(cls.root / "meta-runs")
        cls.core_package, cls.meta_package = cls.root / "core-package", cls.root / "meta-package"
        export_evidence(Path(cls.core["evidence_path"]), cls.core_package)
        export_evidence(Path(cls.meta["evidence_path"]), cls.meta_package)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def copy_package(self, source=None):
        target = self.work / "package"
        shutil.copytree(source or self.core_package, target)
        return target

    def test_core_preserves_original_bytes_and_reproduces_recorded_proof_without_writes(self):
        package = self.copy_package()
        before = hashes(package)
        source_bytes = Path(self.core["evidence_path"]).read_bytes()
        for path in package.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD)
        try:
            result = verify_evidence(package)
        finally:
            for path in package.rglob("*"):
                if path.is_file():
                    path.chmod(stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["gates"], self.core["gates"])
        self.assertEqual(result["evidence_kind"], "deterministic_test_fixture")
        self.assertEqual(result["gates"]["G3"], "PENDING")
        self.assertEqual((package / "artifacts/evidence.json").read_bytes(), source_bytes)
        self.assertEqual(hashes(package), before)

    def test_meta_runtime_package_includes_snapshots_and_excludes_spending_and_credentials(self):
        result = verify_evidence(self.meta_package)
        self.assertEqual(result["runtime_status"], "COMPLETED")
        self.assertEqual(result["gates"]["G7_AI"], "PENDING")
        self.assertEqual(result["evidence_kind"], "deterministic_test_fixture")
        names = hashes(self.meta_package)
        for name in ("artifacts/red.sqlite3", "artifacts/reattack.sqlite3", "artifacts/replay/trace.json"):
            self.assertIn(name, names)
        self.assertFalse(any("spending" in name or ".env" in name or "authorization" in name for name in names))
        self.assertEqual((self.meta_package / "artifacts/evidence.json").read_bytes(), Path(self.meta["evidence_path"]).read_bytes())

    def test_codex_runtime_fixture_retains_sqlite_verification_and_pending_competition_gates(self):
        # Schema compatibility only; preserve explicit offline call provenance.
        source = self.work / "codex-source"
        shutil.copytree(Path(self.meta["evidence_path"]).parent, source)
        data = deepcopy(self.meta)
        data.update(provider="codex", execution_mode="codex_policy_repair_cycle")
        for role in ("red", "blue", "reattack"):
            for call in data["stages"][role]["model_calls"]:
                call["provenance"]["provider"] = "codex"
        data.pop("bundle_hash")
        data["bundle_hash"] = digest(data)
        write_json(source / "evidence.json", data)
        package = self.work / "codex-package"
        export_evidence(source / "evidence.json", package)
        verdict = verify_evidence(package)
        self.assertEqual(verdict["runtime_status"], "COMPLETED")
        self.assertEqual(verdict["evidence_kind"], "deterministic_test_fixture")
        self.assertEqual(verdict["gates"]["G3"], "PENDING")
        artifact = package / "artifacts/evidence.json"
        data["gates"]["G3"] = "PASS"
        write_json(artifact, data)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "Codex competition gate G3"):
            verify_evidence(package)
        data["gates"]["G3"] = "PENDING"
        data["stages"]["red"]["model_calls"][0]["provenance"]["provider"] = "meta"
        write_json(artifact, data)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "invalid recorded model provenance"):
            verify_evidence(package)
        data.update(provider=None, execution_mode="unknown_runtime")
        write_json(artifact, data)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "unsupported evidence format"):
            verify_evidence(package)

    def test_competition_export_rejects_dirty_source_and_verifier_before_copy(self):
        source = self.work / "source-fixture"
        shutil.copytree(Path(self.core["evidence_path"]).parent, source)
        data = json.loads((source / "evidence.json").read_text(encoding="utf-8"))
        for dirty_source in (True, False):
            data["working_tree_dirty"] = dirty_source
            data.pop("bundle_hash", None)
            data["bundle_hash"] = digest(data)
            write_json(source / "evidence.json", data)
            with patch("apps.control.sentinel.evidence.portable._git", return_value=" M unit-fixture.py"), \
                 self.assertRaisesRegex(ValueError, "clean source run" if dirty_source else "clean verifier checkout"):
                export_evidence(source / "evidence.json", self.work / "competition-package", competition=True)
            self.assertFalse((self.work / "competition-package").exists())

    def test_dirty_development_package_cannot_be_reclassified_as_competition_grade(self):
        package = self.copy_package()
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(export_grade="competition", verifier_working_tree_dirty=True)
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "clean source and verifier provenance"):
            verify_evidence(package)

    def test_blocked_and_provider_rejected_partial_runs_remain_partial(self):
        with patch.dict(os.environ, {"MODEL_API_KEY": ""}):
            blocked = run_meta(self.work / "blocked-run")
        blocked_package = self.work / "blocked-package"
        export_evidence(Path(blocked["evidence_path"]), blocked_package)
        result = verify_evidence(blocked_package)
        self.assertEqual(result["runtime_status"], "BLOCKED")
        self.assertEqual(set(result["gates"].values()), {"PENDING"})
        harness = FixtureHarness(red_mode="blocked")
        directory = self.work / "provider-rejected"
        budget = RuntimeBudget(self.work / "ledger.sqlite3", "partial-fixture", approval(), prices())
        partial = _execute_cycle(directory, budget, MODELS, 0, driver_factory=harness.driver,
                                 synthesizer_factory=harness.synthesizer, evidence_kind="deterministic_test_fixture")
        package = self.work / "provider-rejected-package"
        export_evidence(Path(partial["evidence_path"]), package)
        result = verify_evidence(package)
        self.assertEqual(result["runtime_status"], "STOPPED")
        self.assertEqual(result["gates"]["G3"], "PENDING")
        self.assertEqual(json.loads((package / "artifacts/evidence.json").read_text())["error_category"], "PROVIDER_BLOCKED")

    def test_rejected_overblocking_candidate_and_failed_verification_receipt_are_portable(self):
        for fail_before_report in (False, True):
            directory = self.work / ("receipt-only" if fail_before_report else "overblocking")
            harness = FixtureHarness(overblocking=True)
            budget = RuntimeBudget(self.work / (directory.name + "-ledger.sqlite3"), directory.name, approval(), prices())
            context = (patch("apps.control.sentinel.policy.versions.evaluate_candidate", side_effect=ValueError("Explicit verification failure fixture"))
                       if fail_before_report else patch("socket.socket.connect", side_effect=AssertionError("No network")))
            with context:
                partial = _execute_cycle(directory, budget, MODELS, 0, driver_factory=harness.driver,
                                         synthesizer_factory=harness.synthesizer, evidence_kind="deterministic_test_fixture")
            package = self.work / (directory.name + "-package")
            export_evidence(Path(partial["evidence_path"]), package)
            result = verify_evidence(package)
            self.assertEqual(result["runtime_status"], "CANDIDATE_REJECTED")
            self.assertEqual(result["gates"]["G5"], "FAIL")
            self.assertEqual(result["gates"]["G7_AI"], "PENDING")

    def test_missing_extra_and_modified_artifacts_fail_integrity(self):
        for mode in ("missing", "extra", "modified"):
            package = self.work / mode
            shutil.copytree(self.core_package, package)
            if mode == "missing":
                (package / "artifacts/safe-repair.sqlite3").unlink()
            elif mode == "extra":
                (package / ".env").write_text("UNEXPECTED_FILE=1\n")
            else:
                with (package / "artifacts/time-machine/trace.json").open("ab") as output:
                    output.write(b" ")
            with self.assertRaises(ValueError):
                verify_evidence(package)

    def test_rehashed_false_delta_is_rejected_by_committed_transition(self):
        package = self.copy_package()
        path = package / "artifacts/evidence.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        event = evidence["safe_repair_after_promotion"]["events"][0]
        event["actual_delta"] = []
        event.pop("event_hash")
        event["event_hash"] = digest(event)
        write_json(path, evidence)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "actual delta mismatch"):
            verify_evidence(package)

    def test_rehashed_negative_event_duration_is_rejected(self):
        package = self.copy_package()
        path = package / "artifacts/evidence.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        event = evidence["safe_repair_after_promotion"]["events"][0]
        event["duration_seconds"] = -1
        event.pop("event_hash")
        event["event_hash"] = digest(event)
        write_json(path, evidence)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "invalid event duration"):
            verify_evidence(package)

    def test_rehashed_sqlite_tamper_is_rejected_by_snapshot_binding(self):
        package = self.copy_package()
        path = package / "artifacts/safe-repair.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            state = json.loads(db.execute("SELECT snapshot FROM sandbox_state").fetchone()[0])
            state["version"] += 1
            db.execute("UPDATE sandbox_state SET snapshot=?", (json.dumps(state),))
        reseal_package(package, update_evidence=False)
        with self.assertRaisesRegex(ValueError, "safe repair SQLite state mismatch"):
            verify_evidence(package)

    def test_blue_provenance_and_fixture_ai_gate_forgery_are_rejected(self):
        for mode in ("blue", "gate"):
            package = self.work / mode
            shutil.copytree(self.meta_package, package)
            path = package / "artifacts/evidence.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            if mode == "blue":
                data["stages"]["blue"]["model_calls"][0]["provenance"]["real_model_call"] = True
            else:
                data["gates"]["G3"] = "PASS"
            write_json(path, data)
            reseal_package(package)
            with self.assertRaises(ValueError):
                verify_evidence(package)

    def test_rehashed_bundle_cannot_claim_g8_acceptance(self):
        for source in (self.core_package, self.meta_package):
            with self.subTest(source=source.name):
                package = self.work / (source.name + "-g8")
                shutil.copytree(source, package)
                path = package / "artifacts/evidence.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                data["gates"]["G8"] = "PASS"
                write_json(path, data)
                reseal_package(package)
                with self.assertRaisesRegex(ValueError, "cannot establish submission and judging acceptance"):
                    verify_evidence(package)

    def test_rehashed_genuine_claim_without_calls_cannot_establish_an_ai_gate(self):
        package = self.copy_package(self.meta_package)
        path = package / "artifacts/evidence.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(evidence_kind="genuine_live_provider_run", real_model_call=True)
        data["gates"]["G3"] = "PASS"
        for role in ("red", "blue", "reattack"):
            data["stages"][role]["model_calls"] = []
        # Isolate the false aggregate call claim from the separate assurance checks.
        data.update(status="FAIL", policy_assurance_status="INCONCLUSIVE", re_attack_outcome="INCONCLUSIVE")
        data["runtime_checks"].update(fresh_re_attack_completed=False, no_verified_counterexample_on_re_attack=False)
        write_json(path, data)
        reseal_package(package)
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["evidence_kind"] = data["evidence_kind"]
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "aggregate inference provenance mismatch"):
            verify_evidence(package)

    def test_rehashed_assurance_claim_must_match_the_recorded_experiment(self):
        package = self.copy_package(self.meta_package)
        path = package / "artifacts/evidence.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["policy_assurance_status"] = "BYPASSED"
        write_json(path, data)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "recorded policy_assurance_status mismatch"):
            verify_evidence(package)

    def test_rehashed_pipeline_status_must_match_the_recorded_stop(self):
        with patch.dict(os.environ, {"MODEL_API_KEY": ""}):
            source = run_meta(self.work / "blocked-runtime")
        package = self.work / "blocked-package"
        export_evidence(Path(source["evidence_path"]), package)
        data = json.loads((package / "artifacts/evidence.json").read_text(encoding="utf-8"))
        data["pipeline_status"] = "COMPLETED"
        write_json(package / "artifacts/evidence.json", data)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "recorded pipeline status mismatch"):
            verify_evidence(package)

    def test_legacy_overall_pass_cannot_hide_an_actual_sqlite_bypass(self):
        harness = FixtureHarness(narrow=True, reattack_mode="maximal")
        directory = self.work / "bypass-fixture"
        budget = RuntimeBudget(self.work / "fixture-ledger.sqlite3", directory.name, approval(), prices())
        source = _execute_cycle(directory, budget, MODELS, 0, driver_factory=harness.driver,
                                synthesizer_factory=harness.synthesizer, evidence_kind="deterministic_test_fixture")
        package = self.work / "bypass-package"
        export_evidence(Path(source["evidence_path"]), package)
        self.assertEqual(verify_evidence(package)["original_status"], "FAIL")
        data = json.loads((package / "artifacts/evidence.json").read_text(encoding="utf-8"))
        data.pop("policy_assurance_status")
        data.pop("pipeline_status")
        data["runtime_checks"].pop("no_verified_counterexample_on_re_attack")
        data["status"] = "PASS"
        write_json(package / "artifacts/evidence.json", data)
        reseal_package(package)
        with self.assertRaisesRegex(ValueError, "unsuccessful policy assurance"):
            verify_evidence(package)

    def test_rehashed_manifest_cannot_omit_a_recorded_agent_snapshot(self):
        package = self.copy_package(self.meta_package)
        (package / "artifacts/red.sqlite3").unlink()
        path = package / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        del manifest["files"]["artifacts/red.sqlite3"]
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "allowlisted artifact set mismatch"):
            verify_evidence(package)

    def test_absolute_historical_paths_are_inert_and_current_directory_is_irrelevant(self):
        package = self.copy_package()
        previous = Path.cwd()
        try:
            os.chdir(self.work)
            with patch("socket.socket.connect", side_effect=AssertionError("No network")):
                result = verify_evidence(package)
        finally:
            os.chdir(previous)
        self.assertEqual(result["status"], "VERIFIED")
        # The source paths point outside this package; verification only opens allowlisted artifacts.
        evidence = json.loads((package / "artifacts/evidence.json").read_text(encoding="utf-8"))
        self.assertNotEqual(Path(evidence["candidate"]["receipt"]["verification_path"]).parent,
                            package / "artifacts/candidate-verification")

    def test_readonly_command_uses_only_standard_library_and_does_not_load_inference(self):
        environment = dict(os.environ)
        environment.pop("MODEL_API_KEY", None)
        program = ("import sys,socket,runpy; "
                   "socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('Network forbidden')); "
                   "sys.argv=['verify_evidence',sys.argv[1]]; "
                   "runpy.run_module('scripts.verify_evidence',run_name='__main__')")
        result = subprocess.run([sys.executable, "-S", "-B", "-c", program, str(self.meta_package)],
                                cwd=ROOT, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "VERIFIED")
        imports = subprocess.run([sys.executable, "-S", "-B", "-c",
                                  "import sys; import apps.control.sentinel.evidence.portable; "
                                  "assert 'httpx' not in sys.modules; assert 'apps.control.sentinel.agents.meta' not in sys.modules"],
                                 cwd=ROOT, env=environment, capture_output=True, text=True)
        self.assertEqual(imports.returncode, 0, imports.stderr)

    def test_manifest_path_traversal_and_unresolved_source_commit_are_rejected(self):
        package = self.copy_package()
        path = package / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["files"]["../outside.json"] = "0" * 64
        write_json(path, manifest)
        with self.assertRaises(ValueError):
            verify_evidence(package)
        source = self.work / "unresolved"
        shutil.copytree(Path(self.core["evidence_path"]).parent, source)
        evidence = deepcopy(self.core)
        evidence["project_git_commit"] = "0" * 40
        evidence.pop("bundle_hash")
        evidence["bundle_hash"] = digest(evidence)
        write_json(source / "evidence.json", evidence)
        with self.assertRaises(ValueError):
            export_evidence(source / "evidence.json", self.work / "invalid-package")

    def test_package_limits_fail_before_hashing_or_opening_sqlite(self):
        from apps.control.sentinel.evidence import portable
        for limit in ("MAX_ARTIFACT_BYTES", "MAX_PACKAGE_BYTES", "MAX_PACKAGE_FILES", "MAX_PACKAGE_ENTRIES"):
            with self.subTest(limit=limit), patch.object(portable, limit, 1), \
                    patch.object(portable, "_hash", side_effect=AssertionError("Hashing must not start")), \
                    patch.object(portable, "_database", side_effect=AssertionError("SQLite must not open")):
                with self.assertRaisesRegex(ValueError, "limit"):
                    verify_evidence(self.core_package)

    def test_json_size_and_package_depth_are_bounded(self):
        from apps.control.sentinel.evidence import portable
        with patch.object(portable, "MAX_JSON_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "file-size limit"):
                verify_evidence(self.core_package)
        package = self.copy_package()
        nested = package
        for _ in range(portable.MAX_PATH_DEPTH + 1):
            nested /= "nested"
        nested.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "directory-depth limit"):
            verify_evidence(package)

    def test_json_read_limit_survives_a_stale_size_observation(self):
        from apps.control.sentinel.evidence import portable
        path = self.work / "growing.json"
        path.write_text('{"value":"larger-than-the-observed-size"}', encoding="utf-8")
        with patch.object(portable, "MAX_JSON_BYTES", 4), patch.object(portable, "_size", return_value=0):
            with self.assertRaisesRegex(ValueError, "file-size limit"):
                portable._json(path)

    def test_export_scans_report_credentials_before_creating_output(self):
        source = self.work / "source"
        shutil.copytree(Path(self.core["evidence_path"]).parent, source)
        report_path = source / "candidate-verification/evidence.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["diagnostic"] = {"api_key": "explicit-negative-test-value"}
        write_json(report_path, report)
        output = self.work / "rejected-package"
        with self.assertRaisesRegex(ValueError, "credential field"):
            export_evidence(source / "evidence.json", output)
        self.assertFalse(output.exists())

    def test_sqlite_credentials_and_unexpected_payload_tables_are_rejected(self):
        package = self.copy_package()
        snapshot = package / "artifacts/safe-repair.sqlite3"
        with closing(sqlite3.connect(snapshot)) as db, db:
            db.execute("ALTER TABLE sandbox_state ADD COLUMN diagnostic TEXT")
            db.execute("UPDATE sandbox_state SET diagnostic=?", (json.dumps({"access_token": "explicit-negative-test-value"}),))
        reseal_package(package, update_evidence=False)
        with self.assertRaisesRegex(ValueError, "credential field"):
            verify_evidence(package)
        with closing(sqlite3.connect(snapshot)) as db, db:
            db.execute("CREATE TABLE unrelated_payload (value TEXT)")
        reseal_package(package, update_evidence=False)
        with self.assertRaisesRegex(ValueError, "SQLite table set"):
            verify_evidence(package)

    def test_secret_patterns_are_rejected_and_privacy_counts_never_echo_values(self):
        from apps.control.sentinel.evidence.portable import _no_credentials
        for value in ("sk-" + "X" * 30, "LLM_" + "X" * 30,
                      "Bearer " + "X" * 30, "-----BEGIN PRIVATE KEY-----",
                      "MODEL_API_KEY=" + "X" * 30):
            with self.subTest(kind=value[:7]):
                with self.assertRaisesRegex(ValueError, "credential") as error:
                    _no_credentials({"diagnostic": value})
                self.assertNotIn(value, str(error.exception))
        review = _no_credentials({"historical_path": "C:/Users/Private Person/run/evidence.json",
                                  "contact": "reviewer@example.test"})
        self.assertEqual(review, {"absolute_path_values": 1, "email_values": 1})
        self.assertNotIn("Private Person", json.dumps(review))
        for key in ("apiKey", "X-API-Key", "client_secret"):
            with self.subTest(field=key):
                with self.assertRaisesRegex(ValueError, "credential field"):
                    _no_credentials({key: "explicit-negative-test-value"})

    def test_export_records_explicit_retention_and_privacy_limits_without_redaction(self):
        manifest = json.loads((self.core_package / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["retention_policy"], {"published_evidence": "retain_indefinitely",
                         "automatic_deletion": False, "local_artifact_review_after_days": 30})
        self.assertGreater(manifest["privacy_review"]["absolute_path_values"], 0)
        self.assertEqual(manifest["privacy_review"]["source_history_review"], "REQUIRED")
        result = verify_evidence(self.core_package)
        self.assertEqual(result["privacy_review"], manifest["privacy_review"])
        self.assertEqual(result["resource_limits"]["files"], 1024)

    def test_optional_promotion_request_is_bound_to_verified_context(self):
        source = self.work / "idempotent-source"
        shutil.copytree(Path(self.core["evidence_path"]).parent, source)
        data = json.loads((source / "evidence.json").read_text(encoding="utf-8"))
        fingerprint = digest({"candidate": data["candidate"]["patch"], "initial": data["initial_state_hash"],
                              "task": data["task"], "proposals": data["original_proposals"],
                              "evidence_kind": data["evidence_kind"]})
        with closing(sqlite3.connect(source / "policies.sqlite3")) as db, db:
            db.execute("CREATE TABLE promotion_requests (request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, receipt TEXT NOT NULL)")
            db.execute("INSERT INTO promotion_requests VALUES (?, ?, ?)",
                       ("repair-recorded-run", fingerprint, json.dumps(data["candidate"]["receipt"])))
        package = self.work / "idempotent-package"
        export_evidence(source / "evidence.json", package)
        self.assertEqual(verify_evidence(package)["status"], "VERIFIED")
        with closing(sqlite3.connect(package / "artifacts/policies.sqlite3")) as db, db:
            db.execute("UPDATE promotion_requests SET request_hash=?", ("0" * 64,))
        reseal_package(package, update_evidence=False)
        with self.assertRaisesRegex(ValueError, "promotion request context mismatch"):
            verify_evidence(package)


if __name__ == "__main__":
    unittest.main()

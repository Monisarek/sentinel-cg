import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.control.sentinel.gateway import Gateway
from apps.sandbox.fixtures import load_fixture
from apps.sandbox.state.store import Sandbox
from scripts.prove_g2 import prove_g2, verify_event


class G2ProofTests(unittest.TestCase):
    def test_reproducible_proof_exports_verified_gateway_trace(self):
        with TemporaryDirectory() as directory:
            proof = prove_g2(Path(directory))
            saved = json.loads(Path(proof["evidence_path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "PASS", saved["checks"])
            self.assertTrue(all(saved["checks"].values()))
            for event in saved["enforcement_events"]:
                self.assertTrue(verify_event(event))
            event = saved["enforcement_events"][0]
            event["decision"] = "DENY"
            self.assertFalse(verify_event(event))

    def test_non_json_validation_errors_still_return_verifiable_denials(self):
        with TemporaryDirectory() as directory:
            initial, task = load_fixture()
            path = Path(directory) / "sandbox.sqlite3"
            gateway = Gateway(Sandbox.create(path, initial), task)
            original = path.read_bytes()
            for value in (object(), {1, 2}, float("nan"), b"bytes"):
                event = gateway.execute({"name": "set_service_config", "environment": "staging", "service": task.service, "key": "connection_limit", "value": value})
                self.assertEqual(event["decision"], "DENY")
                self.assertEqual(event["reason"], "validation_error")
                self.assertTrue(verify_event(event))
                self.assertEqual(path.read_bytes(), original)
            event = gateway.execute({"name": "restart_service", "environment": "staging", "service": task.service}, expected_state_version=object())
            self.assertEqual(event["decision"], "DENY")
            self.assertTrue(verify_event(event))
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

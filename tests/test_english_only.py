from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.check_english import audit_repository, unexpected_letters


class EnglishOnlyTests(unittest.TestCase):
    def test_public_repository_passes_the_english_only_guard(self):
        findings = audit_repository(Path(__file__).resolve().parents[1])
        self.assertEqual(findings, [], "\n".join(findings))

    def test_guard_detects_foreign_letters_but_allows_diagrams_and_punctuation(self):
        self.assertEqual(unexpected_letters("Production \u2192 staging \u2014 8 \u2265 6\n\u2502 SAFE \u2502"), [])
        self.assertEqual(unexpected_letters("English\n\u041f\u4e2d\u0627"), [(2, "U+041F"), (2, "U+4E2D"), (2, "U+0627")])

    def test_public_text_is_checked_while_dependencies_raw_data_and_binaries_are_exempt(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            names = [
                "prompts/red.md", "apps/web/button.tsx", "node_modules/vendor.js",
                "fixtures/user_inputs/request.txt", "fixtures/protocol_values/wire.json",
                "attachment.docx", "binary.txt",
            ]
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\u041f", encoding="utf-8")
            (root / "binary.txt").write_bytes(b"\0\xff")
            listed = ("\0".join(names) + "\0").encode("utf-8")
            with patch("scripts.check_english.subprocess.check_output", return_value=listed):
                findings = audit_repository(root)
            self.assertEqual(len(findings), 2)
            self.assertTrue(any(message.startswith("prompts/red.md:1:") for message in findings))
            self.assertTrue(any(message.startswith("apps/web/button.tsx:1:") for message in findings))

    def test_non_english_file_names_and_invalid_text_encoding_are_reported(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            name = "\u041f.md"
            (root / name).write_text("English", encoding="utf-8")
            (root / "bad.md").write_bytes(b"\xff")
            listed = (name + "\0bad.md\0").encode("utf-8")
            with patch("scripts.check_english.subprocess.check_output", return_value=listed):
                findings = audit_repository(root)
            self.assertTrue(any("file name" in message for message in findings))
            self.assertTrue(any("UTF-8" in message for message in findings))


if __name__ == "__main__":
    unittest.main()

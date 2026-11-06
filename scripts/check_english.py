"""Catch accidental non-English scripts in public repository text."""

from pathlib import Path
import subprocess
import unicodedata


TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".mdx", ".txt",
    ".html", ".css", ".scss", ".json", ".yaml", ".yml", ".toml",
    ".svg", ".srt", ".vtt", ".sh", ".ps1",
}
EXCLUDED_PARTS = {
    ".git", ".cache", ".venv", ".codex", ".agents", "__pycache__",
    "node_modules", "vendor", "third_party", "third-party", "dist", "build",
    "attachments", "user_inputs", "user-inputs", "protocol_values", "protocol-values",
}


def unexpected_letters(text: str) -> list[tuple[int, str]]:
    # shortcut: character checks cannot classify ASCII-only foreign prose; review wording manually.
    return [
        (number, f"U+{ord(character):04X}")
        for number, line in enumerate(text.splitlines(), 1)
        for character in line
        if not character.isascii() and unicodedata.category(character).startswith("L")
    ]


def audit_repository(root: Path) -> list[str]:
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    ).decode("utf-8").split("\0")
    findings = []
    for name in sorted(set(names) - {""}):
        relative = Path(name)
        if EXCLUDED_PARTS.intersection(relative.parts):
            continue
        if relative.suffix.lower() not in TEXT_SUFFIXES and relative.name not in {
            "LICENSE", "Makefile", ".env.example", ".gitignore", ".gitattributes",
        }:
            continue
        path = root / relative
        if not path.is_file():
            continue
        for _, codepoint in unexpected_letters(name):
            findings.append(f"{name}: unexpected non-ASCII letter in file name ({codepoint})")
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            findings.append(f"{name}: public text must use UTF-8")
            continue
        for line, codepoint in unexpected_letters(text):
            findings.append(f"{name}:{line}: unexpected non-ASCII letter ({codepoint})")
    return findings


def main() -> int:
    findings = audit_repository(Path(__file__).resolve().parents[1])
    if findings:
        print("\n".join(findings))
        return 1
    print("English-only guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path


REQUIRED_PATTERNS = {
    "\u670d\u52a1\u5668\u4fe1\u606f.txt",
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "*.token",
    "*.secret",
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "mlruns",
    ".minio",
}


def test_dockerignore_exists_and_excludes_sensitive_inputs() -> None:
    path = Path(".dockerignore")

    assert path.exists()
    lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}

    assert REQUIRED_PATTERNS.issubset(lines)


def test_dockerignore_keeps_source_and_tests_available() -> None:
    text = Path(".dockerignore").read_text(encoding="utf-8")

    assert "\napps\n" not in f"\n{text}\n"
    assert "\npackages\n" not in f"\n{text}\n"
    assert "\nscripts\n" not in f"\n{text}\n"
    assert "\ntests\n" not in f"\n{text}\n"

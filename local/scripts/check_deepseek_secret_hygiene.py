from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REQUIRED_GITIGNORE_ENTRIES = {
    ".env",
    "*.env",
    ".env.local",
    ".env.*.local",
}

PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change_me",
    "placeholder",
    "your_key_here",
    "your-api-key",
    "example",
    "none",
}

ENV_KEY_NAMES = {
    "DEEPSEEK_API_KEY",
    "BING_SEARCH_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "SERPAPI_API_KEY",
    "TAVILY_API_KEY",
    "EXA_API_KEY",
}

SCAN_ROOTS = ("reports", "docs", "tests")
SCAN_FILES = ("README.md",)
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}

HIGH_RISK_PATTERNS = {
    "deepseek_or_openai_style_key": re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b"),
    "assigned_api_key_value": re.compile(
        r"\b(?:DEEPSEEK|BING|BRAVE|SERPAPI|TAVILY|EXA)_[A-Z_]*API_KEY\s*[:=]\s*['\"]?([^'\"\s#]{12,})",
        re.IGNORECASE,
    ),
    "assigned_token_value": re.compile(
        r"\b(?:token|password|secret|credential)\s*[:=]\s*['\"]?([^'\"\s#]{12,})",
        re.IGNORECASE,
    ),
}

RAW_PAYLOAD_PATTERNS = {
    "raw_html": re.compile(r"<html[\s>]", re.IGNORECASE),
    "raw_text_field": re.compile(r"\braw_text\b", re.IGNORECASE),
    "full_prompt_field": re.compile(r"\b(full_)?prompt\b", re.IGNORECASE),
    "full_completion_field": re.compile(r"\b(full_)?completion\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class Finding:
    path: str
    finding_type: str
    severity: str
    line: int | None = None


def run_audit(output_dir: str = "reports/deepseek_web_search") -> dict:
    findings: list[Finding] = []
    findings.extend(_check_gitignore())
    findings.extend(_check_env_example())
    findings.extend(_scan_project_text())
    summary = {
        "status": "passed" if not any(item.severity == "high" for item in findings) else "failed",
        "finding_count": len(findings),
        "high_count": sum(1 for item in findings if item.severity == "high"),
        "medium_count": sum(1 for item in findings if item.severity == "medium"),
        "low_count": sum(1 for item in findings if item.severity == "low"),
        "findings": [asdict(item) for item in findings],
        "notes": [
            "This audit does not print or store secret values.",
            "The local .env file is not read; only ignore rules and public placeholder files are checked.",
        ],
    }
    _write_reports(summary, Path(output_dir))
    return summary


def _check_gitignore() -> list[Finding]:
    path = Path(".gitignore")
    if not path.exists():
        return [Finding(".gitignore", "missing_gitignore", "high")]
    entries = {
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    findings: list[Finding] = []
    for required in sorted(REQUIRED_GITIGNORE_ENTRIES):
        if required not in entries:
            findings.append(Finding(".gitignore", f"missing_ignore_rule:{required}", "high"))
    return findings


def _check_env_example() -> list[Finding]:
    path = Path(".env.example")
    if not path.exists():
        return [Finding(".env.example", "missing_env_example", "medium")]
    findings: list[Finding] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        is_credential_key = key in ENV_KEY_NAMES or any(part in key.lower() for part in ("token", "password", "secret"))
        if key in ENV_KEY_NAMES and value.lower() not in PLACEHOLDER_VALUES:
            findings.append(Finding(str(path), f"non_placeholder_value:{key}", "high", line_no))
        if is_credential_key and _looks_like_real_secret(value):
            findings.append(Finding(str(path), "secret_like_value", "high", line_no))
    return findings


def _scan_project_text() -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_scan_files():
        if not path.exists() or path.name in {".env", ".env.example"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            for finding_type, pattern in HIGH_RISK_PATTERNS.items():
                match = pattern.search(line)
                if match and _match_is_real_secret(match):
                    findings.append(Finding(str(path), finding_type, "high", line_no))
            if "reports/deepseek_web_search" in str(path):
                for finding_type, pattern in RAW_PAYLOAD_PATTERNS.items():
                    if pattern.search(line):
                        findings.append(Finding(str(path), finding_type, "medium", line_no))
    return findings


def _iter_scan_files() -> Iterable[Path]:
    for name in SCAN_FILES:
        path = Path(name)
        if path.exists():
            yield path
    for root_name in SCAN_ROOTS:
        root = Path(root_name)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in {".md", ".json", ".txt", ".py", ".yml", ".yaml"}:
                continue
            yield path


def _match_is_real_secret(match: re.Match[str]) -> bool:
    groups = [group for group in match.groups() if group]
    if not groups:
        return True
    return any(_looks_like_real_secret(group) for group in groups)


def _looks_like_real_secret(value: str) -> bool:
    normalized = str(value or "").strip().strip("\"'").lower()
    if normalized in PLACEHOLDER_VALUES:
        return False
    if normalized.startswith("${") and normalized.endswith("}"):
        return False
    if normalized.startswith("<") and normalized.endswith(">"):
        return False
    if normalized.startswith("sk-") and len(normalized) >= 20:
        return True
    if len(normalized) >= 24 and re.search(r"[a-z]", normalized) and re.search(r"\d", normalized):
        return True
    return False


def _write_reports(summary: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "SECRET_HYGIENE_AUDIT.json"
    md_path = output_dir / "SECRET_HYGIENE_AUDIT.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_lines = [
        "# DeepSeek Secret Hygiene Audit",
        "",
        f"Status: `{summary['status']}`",
        f"Findings: `{summary['finding_count']}`",
        f"High: `{summary['high_count']}`",
        f"Medium: `{summary['medium_count']}`",
        "",
        "No secret values are printed or stored by this audit.",
        "",
        "## Findings",
    ]
    if not summary["findings"]:
        md_lines.append("")
        md_lines.append("- None")
    for item in summary["findings"]:
        md_lines.append(
            f"- `{item['severity']}` `{item['finding_type']}` in `{item['path']}`"
            + (f" line `{item['line']}`" if item.get("line") else "")
        )
    md_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check DeepSeek local secret hygiene without printing secrets.")
    parser.add_argument("--output-dir", default="reports/deepseek_web_search")
    args = parser.parse_args(argv)
    summary = run_audit(args.output_dir)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "finding_count": summary["finding_count"],
                "high_count": summary["high_count"],
                "medium_count": summary["medium_count"],
                "audit_json": str(Path(args.output_dir) / "SECRET_HYGIENE_AUDIT.json"),
                "audit_markdown": str(Path(args.output_dir) / "SECRET_HYGIENE_AUDIT.md"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

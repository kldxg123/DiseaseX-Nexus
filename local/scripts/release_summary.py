from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RELEASE_DIR = Path("reports/release_readiness")
REQUIRED_REPORTS = (
    "SYSTEM_ACCEPTANCE_REPORT.md",
    "FEATURE_MATRIX.md",
    "TEST_AND_VALIDATION_REPORT.md",
    "SECURITY_BOUNDARY_REPORT.md",
    "NEXT_PHASE_PLAN.md",
    "HANDOFF_COMMANDS.md",
    "KNOWN_LIMITATIONS.md",
)
REQUIRED_PHRASES = ("本地 MVP", "真实数据未验证", "人工复核")
ACCURACY_PHRASES = ("不保证预测准确率", "不保证未来准确率", "不承诺真实预测准确率")
FORBIDDEN_SNIPPETS = ("SSH_KEY_PATH", "password=", "token=", "secret=", "private key")


def main(argv: list[str] | None = None) -> int:
    _ = argv
    payload = build_release_summary()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


def build_release_summary(base_dir: str | Path = RELEASE_DIR) -> dict[str, Any]:
    directory = Path(base_dir)
    reports_present: list[str] = []
    missing_reports: list[str] = []
    warnings: list[str] = []

    for report_name in REQUIRED_REPORTS:
        path = directory / report_name
        if path.exists():
            reports_present.append(report_name)
            warnings.extend(_validate_report_text(path))
        else:
            missing_reports.append(report_name)

    status = "ok" if not missing_reports and not warnings else "failed"
    return {
        "status": status,
        "reports_present": reports_present,
        "missing_reports": missing_reports,
        "warnings": warnings,
    }


def _validate_report_text(path: Path) -> list[str]:
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [f"{path.name}: unreadable"]
    for phrase in REQUIRED_PHRASES:
        if phrase not in text:
            warnings.append(f"{path.name}: missing required release phrase")
            break
    if not any(phrase in text for phrase in ACCURACY_PHRASES):
        warnings.append(f"{path.name}: missing accuracy limitation phrase")
    lowered = text.lower()
    if any(snippet.lower() in lowered for snippet in FORBIDDEN_SNIPPETS):
        warnings.append(f"{path.name}: contains forbidden sensitive marker")
    return warnings


if __name__ == "__main__":
    raise SystemExit(main())

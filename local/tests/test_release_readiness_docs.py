from __future__ import annotations

from pathlib import Path


RELEASE_DIR = Path("reports/release_readiness")
REQUIRED_REPORTS = [
    "SYSTEM_ACCEPTANCE_REPORT.md",
    "FEATURE_MATRIX.md",
    "TEST_AND_VALIDATION_REPORT.md",
    "SECURITY_BOUNDARY_REPORT.md",
    "NEXT_PHASE_PLAN.md",
    "HANDOFF_COMMANDS.md",
    "KNOWN_LIMITATIONS.md",
]
FORBIDDEN_SNIPPETS = (
    "SSH_KEY_PATH",
    "password=",
    "token=",
    "secret=",
    "private key",
)


def test_release_readiness_reports_exist_and_include_required_boundaries() -> None:
    assert RELEASE_DIR.exists()
    for report_name in REQUIRED_REPORTS:
        path = RELEASE_DIR / report_name
        assert path.exists(), report_name
        text = path.read_text(encoding="utf-8")
        assert "本地 MVP" in text
        assert "真实数据未验证" in text
        assert "人工复核" in text
        assert ("不保证预测准确率" in text) or ("不承诺真实预测准确率" in text)


def test_release_readiness_reports_do_not_contain_sensitive_markers() -> None:
    for path in RELEASE_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet.lower() not in text

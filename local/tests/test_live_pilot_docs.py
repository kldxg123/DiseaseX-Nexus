from __future__ import annotations

from pathlib import Path


DOCS = [
    Path("docs/live_data_pilot/CDC_NOROSTAT_LIVE_DRY_RUN_RUNBOOK.md"),
    Path("docs/live_data_pilot/CDC_NOROSTAT_PREFLIGHT_CHECKLIST.md"),
    Path("docs/live_data_pilot/CDC_NOROSTAT_ACCEPTANCE_CRITERIA.md"),
    Path("docs/live_data_pilot/CDC_NOROSTAT_ROLLBACK_AND_CLEANUP.md"),
]


def test_live_pilot_docs_exist_and_state_safety_boundaries() -> None:
    for path in DOCS:
        assert path.exists()
        text = path.read_text(encoding="utf-8").lower()
        assert "manual review" in text
        assert "robots" in text
        assert "terms" in text
        assert "max_requests" in text or "max requests" in text
        assert "not execute" in text or "not executed" in text or "does not access" in text
        assert "prediction accuracy" in text or "guarantee" in text


def test_live_pilot_docs_do_not_contain_sensitive_markers() -> None:
    for path in DOCS:
        text = path.read_text(encoding="utf-8").lower()
        for snippet in ("ssh_key_path", "password=", "token=", "secret=", "private key"):
            assert snippet not in text

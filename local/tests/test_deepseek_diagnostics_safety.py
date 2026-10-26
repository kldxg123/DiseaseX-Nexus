from __future__ import annotations

import argparse
import re

from scripts.replay_deepseek_structured_extraction import run_replay
from tests.test_deepseek_structured_extraction_replay import make_safe_external_run_dir


SECRET_LIKE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b")


def test_replay_reports_do_not_save_sensitive_or_raw_payloads(tmp_path) -> None:
    run_dir = make_safe_external_run_dir(tmp_path)
    output_dir = tmp_path / "reports"
    summary = run_replay(
        argparse.Namespace(
            run_dir=str(run_dir),
            allow_deepseek_api=False,
            database_url=f"sqlite:///{(tmp_path / 'safe.sqlite').as_posix()}",
            output_dir=str(output_dir),
            timeout_seconds=5.0,
        )
    )

    rendered = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".md"}
    )
    lowered = rendered.lower()

    assert summary["no_tavily_rerun"] is True
    assert summary["page_read_request_count"] == 0
    assert not SECRET_LIKE.search(rendered)
    assert "<html" not in lowered
    assert "authorization" not in lowered
    assert "raw_text" not in lowered
    assert "metadata_json" not in lowered
    assert "provenance_json" not in lowered
    assert "feature_json" not in lowered
    assert "lesson_json" not in lowered

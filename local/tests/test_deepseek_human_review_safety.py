from __future__ import annotations

import re

from deepseek_web_search_agent.human_review_promotion import (
    export_human_review_template,
    promote_reviewed_records_to_epidemic_events,
)
from tests.deepseek_human_review_fixture import create_human_review_fixture


SECRET_LIKE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b")


def test_human_review_reports_do_not_leak_sensitive_or_raw_payloads(tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    export_human_review_template(report_dir, output_dir)
    promote_reviewed_records_to_epidemic_events(
        report_dir=report_dir,
        annotation_file=output_dir / "deepseek_human_review_template.json",
        output_dir=output_dir,
        allow_persistence=False,
    )

    rendered = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output_dir.glob("*")
        if path.suffix.lower() in {".json", ".md", ".csv"}
    )
    lowered = rendered.lower()
    assert not SECRET_LIKE.search(rendered)
    for forbidden in [
        "api_key",
        "token",
        "password",
        "secret",
        "prompt",
        "completion",
        "response body",
        "raw_text",
        "<html",
        "metadata_json",
        "feature_json",
        "provenance_json",
        "lesson_json",
    ]:
        assert forbidden not in lowered

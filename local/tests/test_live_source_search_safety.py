from __future__ import annotations

import json

from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search


FORBIDDEN = [
    "password",
    "token",
    "secret",
    "ssh_key_path",
    "raw_text",
    "metadata_json",
    "feature_json",
    "provenance_json",
    "lesson_json",
    "response_body",
]


def test_live_source_search_outputs_do_not_expose_sensitive_or_raw_payloads(tmp_path):
    report = run_live_source_search(
        LiveSourceSearchRequest(
            country="United States",
            disease="mystery fever",
            dry_run=True,
            output_dir=str(tmp_path),
        )
    )
    rendered = json.dumps(report.to_dict(), ensure_ascii=False).lower()
    for term in FORBIDDEN:
        assert term not in rendered
    assert report.candidate_urls_visited is False
    assert report.no_follow_links is True

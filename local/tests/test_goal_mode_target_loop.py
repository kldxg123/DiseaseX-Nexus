from __future__ import annotations

import json

from deepseek_web_search_agent.auto_target_runner import parse_targets, run_goal_mode


def test_parse_targets_supports_disease_country_pairs() -> None:
    assert parse_targets("dengue:Global,measles:United States") == [
        ("dengue", "Global"),
        ("measles", "United States"),
    ]


def test_goal_mode_mock_generates_review_rows_and_stops(tmp_path) -> None:
    summary = run_goal_mode(
        targets="dengue:Global,measles:United States",
        date_start="2026-05-01",
        date_end="2026-07-01",
        output_dir=str(tmp_path / "goal"),
        mock_deepseek=True,
        mock_search=True,
        allow_document_preview=True,
    )

    assert summary.status == "ok"
    assert len(summary.attempts) == 1
    assert summary.attempts[0].review_rows_generated >= 1
    assert summary.network_accessed is False
    payload = json.loads(open(summary.goal_mode_summary_json, encoding="utf-8").read())
    assert payload["attempts"][0]["review_rows_generated"] >= 1

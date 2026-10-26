from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx

from scripts.run_controlled_official_acquisition import (
    DEFAULT_FLUVIEW_URL,
    run_controlled_official_acquisition,
)


def test_controlled_official_acquisition_mock_writes_pending_review_pipeline(tmp_path) -> None:
    fixture = Path("tests/fixtures/cdc_fluview_table.html").read_text(encoding="utf-8")
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert str(request.url) == DEFAULT_FLUVIEW_URL
        return httpx.Response(200, text=fixture, headers={"content-type": "text/html"})

    summary = run_controlled_official_acquisition(
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
        output_dir=str(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    assert summary["status"] == "ok"
    assert request_count == 1
    assert summary["request_count"] == 1
    assert summary["follow_links"] is False
    assert summary["candidate_urls_accessed"] is False
    assert summary["source_status"] == "pending_review"
    assert summary["raw_documents_saved"] > 0
    assert summary["epidemic_events_created"] > 0
    assert summary["model_features_created"] >= 0
    assert "not enough live acquisition history to build model features" in summary["warnings"]
    assert Path(summary["summary_json"]).exists()
    assert Path(summary["rollback_commands"]).exists()
    assert Path(summary["data_quality_report"]["json"]).exists()
    assert Path(summary["crawler_optimizer_report"]).exists()

    db_path = Path(summary["output_dir"]) / "controlled_official_acquisition.sqlite"
    with sqlite3.connect(db_path) as connection:
        status_counts = dict(
            connection.execute("select status, count(*) from source_registry group by status").fetchall()
        )
    assert status_counts == {"pending_review": 1}
    _assert_safe(json.dumps(summary, ensure_ascii=False))


def test_controlled_official_acquisition_denies_without_required_flags(tmp_path) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="should not be fetched")

    summary = run_controlled_official_acquisition(
        allow_network=True,
        terms_reviewed=False,
        manual_reviewed=True,
        robots_allowed=True,
        output_dir=str(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    assert summary["status"] == "failed"
    assert summary["network_accessed"] is False
    assert summary["guardrail_allowed"] is False
    assert called is False
    _assert_safe(json.dumps(summary, ensure_ascii=False))


def test_controlled_official_acquisition_rejects_unallowlisted_url(tmp_path) -> None:
    summary = run_controlled_official_acquisition(
        live_url="https://example.invalid/fluview",
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        output_dir=str(tmp_path),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="blocked")),
    )

    assert summary["status"] == "failed"
    assert summary["network_accessed"] is False
    assert summary["guardrail_allowed"] is False
    _assert_safe(json.dumps(summary, ensure_ascii=False))


def _assert_safe(rendered: str) -> None:
    lowered = rendered.lower()
    for marker in (
        "<html",
        "</html",
        "raw_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "password",
        "token",
        "secret",
        "private key",
    ):
        assert marker not in lowered

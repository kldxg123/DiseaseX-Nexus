from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cleaner_agent.feature_engineering import aggregate_events_weekly


FIXTURE = Path("tests/fixtures/feature_events_norovirus.json")


def _events() -> list[SimpleNamespace]:
    return [SimpleNamespace(**row) for row in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def test_weekly_aggregation_sums_counts_and_document_categories() -> None:
    aggregates = aggregate_events_weekly(_events(), fill_missing_weeks=True)
    by_week = {aggregate.iso_week: aggregate for aggregate in aggregates}

    assert len(aggregates) == 12
    assert by_week["2025-W02"].confirmed_cases == 2
    assert by_week["2025-W02"].suspected_cases == 1
    assert by_week["2025-W02"].cases == 3
    assert by_week["2025-W02"].document_count == 1
    assert by_week["2025-W02"].media_document_count == 1
    assert by_week["2025-W04"].official_document_count == 1
    assert by_week["2025-W03"].cases == 0
    assert by_week["2025-W03"].document_count == 0


def test_weekly_aggregation_without_fill_keeps_observed_weeks_only() -> None:
    aggregates = aggregate_events_weekly(_events(), fill_missing_weeks=False)

    assert len(aggregates) == 10
    assert "2025-W03" not in {aggregate.iso_week for aggregate in aggregates}

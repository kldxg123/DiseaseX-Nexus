from __future__ import annotations

# ruff: noqa: I001

import csv
import gzip
import json
from datetime import date
from pathlib import Path

import r4e_ilinet_pipeline as r4e


REPORT = Path("USER_HOME/projects/openclaw/reports/r4e_cdc_ilinet_ingestion_20260806T1225Z")


def test_mmwr_week_start_known_boundary() -> None:
    assert r4e.mmwr_week_start(2025, 40) == date(2025, 9, 28)
    assert r4e.mmwr_week_start(2026, 1).weekday() == 6


def test_parse_num_missing_tokens() -> None:
    assert r4e.parse_num("X") is None
    assert r4e.parse_num("") is None
    assert r4e.parse_num("1.25") == 1.25


def test_normalized_snapshot_exists_and_schema() -> None:
    profile = json.loads((REPORT / "R4E_NORMALIZED_DATA_PROFILE.json").read_text())
    assert profile["observation_rows"] > 0
    assert profile["region_count"] >= 50
    assert profile["duplicates"] == 0
    norm = REPORT / profile["normalized_path"]
    assert norm.exists()
    with gzip.open(norm, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["source_key"] == "cdc_fluview_ilinet_state_weekly"
    assert row["region_type"] == "States"
    assert row["event_week_start"]
    assert row["percent_weighted_ili"] is not None


def test_contract_preserves_weighted_and_unweighted() -> None:
    contract = json.loads((REPORT / "R4E_SOURCE_CONTRACT.json").read_text())
    assert contract["interface_class"] == "OFFICIAL_INTERACTIVE_EXPORT_UNDOCUMENTED"
    assert "percent_weighted_ili" in contract["metric_names"]
    assert "percent_unweighted_ili" in contract["metric_names"]
    assert contract["historical_vintage_available"] is False
    assert contract["real_time_historical_backtest_ready"] is False


def test_database_safety_invariants() -> None:
    db = json.loads((REPORT / "R4E_DATABASE_BEFORE_AFTER.json").read_text())
    assert db["committed"] is True
    assert db["safety_unchanged"] is True
    assert db["actual_delta"]["forecast_runs"] == 0
    assert db["actual_delta"]["forecast_values"] == 0
    assert db["actual_delta"]["kg_claims_evidence_eligible"] == 0
    assert db["actual_delta"]["kg_claims_recommendation_eligible"] == 0

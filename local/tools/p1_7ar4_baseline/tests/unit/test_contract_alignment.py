from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from epidemic_core.normalization.contract import Contract, CountryMapping, Versions
from epidemic_core.normalization.parser import SourceRow, parse_artifact
from epidemic_core.normalization.service import _quality_flags

ROOT = Path(__file__).parents[2]
HEADER = "Date_reported,Country_code,Country,WHO_region,New_cases,Cumulative_cases,New_deaths,Cumulative_deaths"


def _contract() -> Contract:
    return Contract(
        Versions("who-covid19-transform-v1", "who-covid19-country-map-v1", "quality-v1", 1),
        tuple(HEADER.split(",")),
        {
            "New_cases": "cases_new",
            "Cumulative_cases": "cases_cumulative",
            "New_deaths": "deaths_new",
            "Cumulative_deaths": "deaths_cumulative",
        },
        "who_covid19_global_weekly",
        "https://example.test/data.csv",
        "https://example.test",
        "https://example.test/terms",
        "WHO",
        "No endorsement",
        "who-covid19-normalization-v2",
        "metrics-v2",
    )


def test_v1_is_historical_and_v2_manifest_hashes_are_exact() -> None:
    reference = ROOT / "reference"
    manifest = json.loads((reference / "active_catalogs.v1.json").read_text(encoding="utf-8"))
    assert (reference / "metrics.v1.json").exists()
    assert (reference / "who_covid19_normalization_contract.v1.json").exists()
    for key in ("active_metrics", "active_covid_contract"):
        active = manifest[key]
        assert hashlib.sha256((reference / active["file_name"]).read_bytes()).hexdigest() == active["sha256"]
    assert manifest["active_metrics"]["dataset_key"] == "metrics-v2"
    assert manifest["active_covid_contract"]["dataset_key"] == "who_covid19_normalization_contract_v2"


def test_contract_v2_and_metrics_v2_agree_on_cumulative_revisions() -> None:
    reference = ROOT / "reference"
    contract = json.loads((reference / "who_covid19_normalization_contract.v2.json").read_text())
    metrics = {x["metric_code"]: x for x in json.loads((reference / "metrics.v2.json").read_text())}
    for field, code in (("Cumulative_cases", "cases_cumulative"), ("Cumulative_deaths", "deaths_cumulative")):
        spec = contract["metric_catalog"][field]
        assert spec["metric_code"] == code
        assert spec["allow_negative"] is True and spec["min_value"] is None and spec["schema_version"] == 2
        assert metrics[code]["allow_negative"] is True
        assert metrics[code]["min_value"] is None
        assert metrics[code]["schema_version"] == 2
        assert "never truncated or recomputed" in metrics[code]["description"]


def test_cumulative_decrease_and_negative_values_are_preserved_and_flagged(tmp_path: Path) -> None:
    previous = SourceRow(dt.date(2024, 1, 7), "FR", "France", "EUR", (10, 100, 2, 10), ("10", "100", "2", "10"))
    decreased = SourceRow(dt.date(2024, 1, 14), "FR", "France", "EUR", (8, 98, 1, 9), ("8", "98", "1", "9"))
    decreased_flags = _quality_flags(decreased, previous, decreased.reported)
    assert decreased.values[1:] == (98, 1, 9)
    assert "CUMULATIVE_REVISION" in decreased_flags[1]
    assert "CUMULATIVE_REVISION" in decreased_flags[3]

    path = tmp_path / "negative.csv"
    path.write_text(HEADER + "\n2024-01-21,FR,France,EUR,0,-2,0,-1\n", encoding="utf-8")
    mapping = CountryMapping("who-covid19-country-map-v1", "a" * 64, {"FR": "FR"}, frozenset())
    parsed = parse_artifact(path, _contract(), mapping, "FR", dt.date(2024, 1, 21), dt.date(2024, 1, 21))
    row = parsed.selected_rows[0]
    assert row.values == (0, -2, 0, -1)
    negative_flags = _quality_flags(row, decreased, row.reported)
    assert "NEGATIVE_CUMULATIVE_VALUE" in negative_flags[1]
    assert "NEGATIVE_CUMULATIVE_VALUE" in negative_flags[3]
    assert "CUMULATIVE_REVISION" in negative_flags[1]
    assert "CUMULATIVE_REVISION" in negative_flags[3]

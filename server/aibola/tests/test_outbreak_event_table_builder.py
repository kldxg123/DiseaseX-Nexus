from __future__ import annotations

from pathlib import Path

from src.data.outbreak_risk_dataset import build_event_table


def test_outbreak_event_table_separates_label_and_feature_sources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/outbreak_risk_sources.yaml").write_text(
        "disease_scope: ebola_only\n"
        "sources: {}\n"
        "seed_events:\n"
        "  - outbreak_id: g1\n"
        "    disease: Ebola\n"
        "    pathogen: Zaire ebolavirus\n"
        "    country: Guinea\n"
        "    iso3: GIN\n"
        "    adm1: Nzerekore\n"
        "    region_id: GIN.ADM1.Nzerekore\n"
        "    event_start_date: '2021-02-14'\n"
        "    source_url: https://example.org\n"
        "    usage: external_test_locked\n",
        encoding="utf-8",
    )
    result = build_event_table()
    assert result["outbreak_event_count"] == 1
    rows = (tmp_path / "data/processed/outbreak_risk/outbreak_event_table.csv").read_text(encoding="utf-8")
    assert "not_pre_event_feature" in rows
    assert "external_test_locked" in rows

from __future__ import annotations

from pathlib import Path

from src.data.outbreak_risk_dataset import build_event_table, build_labels, build_negative_samples, build_region_week_panel


def write_minimal_configs(root: Path) -> None:
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs/outbreak_risk_sources.yaml").write_text(
        "disease_scope: ebola_only\n"
        "sources: {}\n"
        "seed_events:\n"
        "  - outbreak_id: e1\n"
        "    disease: Ebola\n"
        "    pathogen: Zaire ebolavirus\n"
        "    country: Guinea\n"
        "    iso3: GIN\n"
        "    adm1: Nzerekore\n"
        "    region_id: GIN.ADM1.Nzerekore\n"
        "    event_start_date: '2021-02-14'\n"
        "    source_url: https://example.org\n"
        "    usage: train_candidate\n",
        encoding="utf-8",
    )
    (root / "configs/outbreak_risk_training.yaml").write_text(
        "disease_scope: ebola_only\n"
        "prototype_thresholds:\n"
        "  outbreak_event_count_min: 1\n"
        "  positive_region_week_count_min: 1\n"
        "  negative_to_positive_min: 1\n"
        "  countries_min: 1\n"
        "  feature_missing_rate_max: 0.4\n"
        "formal_thresholds:\n"
        "  outbreak_event_count_min: 3\n"
        "  positive_region_week_count_min: 10\n"
        "  negative_to_positive_min: 2\n"
        "  countries_min: 2\n"
        "  feature_missing_rate_max: 0.25\n",
        encoding="utf-8",
    )


def test_negative_sampler_reports_matching_strategy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_minimal_configs(tmp_path)
    build_event_table()
    build_region_week_panel()
    build_labels()
    result = build_negative_samples()
    assert result["negative_sample_count"] >= result["positive_sample_count"]
    assert "season" in result["matching_strategy"]
    assert result["negative_type_distribution"]

from __future__ import annotations

from pathlib import Path

from src.data.outbreak_risk_dataset import run_quality_gate
from src.utils.io import write_csv_records, write_json

from tests.test_outbreak_negative_sampler import write_minimal_configs


def test_quality_gate_skips_training_when_positive_rows_too_low(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_minimal_configs(tmp_path)
    write_csv_records(tmp_path / "data/processed/outbreak_risk/outbreak_event_table.csv", [{"outbreak_id": "e1", "iso3": "GIN"}])
    write_csv_records(
        tmp_path / "data/processed/outbreak_risk/outbreak_risk_dataset.csv",
        [{"sample_id": "s1", "split": "train", "label": 0, "iso3": "GIN"}],
    )
    write_csv_records(tmp_path / "data/processed/outbreak_risk/negative_samples.csv", [{"sample_id": "s1"}])
    write_json(tmp_path / "data/outputs/reports/outbreak_feature_build_report.json", {"feature_missing_rate": 0.0})
    write_json(tmp_path / "data/outputs/reports/outbreak_leakage_check_report.json", {"leakage_check": "passed"})
    result = run_quality_gate()
    assert result["prototype_training_allowed"] is False
    assert "positive_region_week_count_min" in result["blocked_reason"]

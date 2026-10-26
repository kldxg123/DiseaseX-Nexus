from __future__ import annotations

from pathlib import Path

from src.data.outbreak_risk_dataset import build_event_table, build_labels, build_region_week_panel

from tests.test_outbreak_negative_sampler import write_minimal_configs


def test_outbreak_label_windows_are_future_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_minimal_configs(tmp_path)
    build_event_table()
    build_region_week_panel()
    result = build_labels()
    assert result["positive_sample_count"] == 4
    labels = (tmp_path / "data/processed/outbreak_risk/outbreak_labels.csv").read_text(encoding="utf-8")
    assert "outbreak_within_4w" in labels
    assert "retrospective_event_label;prototype_only" in labels

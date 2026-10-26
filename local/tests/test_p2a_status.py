from __future__ import annotations

from pathlib import Path

from src.reports.p2a_status import build_p2a_status
from src.utils.io import write_json


def test_p2a_status_reports_supervisor_ready_prototype_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    reports = tmp_path / "data/outputs/reports"
    write_json(reports / "outbreak_data_acquisition_report.json", {"records": [{"status": "downloaded", "source": "WHO"}]})
    write_json(reports / "outbreak_literature_survey_report.json", {"dataset_links": [{"url": "https://example.org"}], "dataset_link_count": 1})
    write_json(reports / "outbreak_event_table_report.json", {"outbreak_event_count": 3})
    write_json(reports / "region_week_panel_report.json", {"region_week_panel_rows": 10})
    write_json(reports / "outbreak_label_quality_report.json", {"positive_sample_count": 2})
    write_json(reports / "outbreak_negative_sampling_report.json", {"negative_sample_count": 6})
    write_json(reports / "outbreak_leakage_check_report.json", {"leakage_check": "passed"})
    write_json(reports / "outbreak_dataset_quality_report.json", {"prototype_training_allowed": False, "formal_training_allowed": False, "prototype_only": True, "blocked_reason": "low_n"})
    write_json(reports / "outbreak_risk_baseline_train_report.json", {"model_trained": "none"})
    write_json(reports / "outbreak_risk_baseline_eval_report.json", {"results": {}})
    write_json(reports / "outbreak_prototype_training_report.json", {"blocked_reason": "low_n"})
    result = build_p2a_status()
    assert result["prototype_only"] is True
    assert result["can_be_used_for_supervisor_reporting"] is True
    assert result["blocked_reason"] == "low_n"

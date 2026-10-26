from __future__ import annotations

import json
from pathlib import Path

from src.reports import outbreak_prediction_feasibility as p2a


def _project_root(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "configs").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_outbreak_event_schema_contains_required_onset_fields() -> None:
    schema = p2a.outbreak_event_table_schema()
    required = set(schema["required_fields"])
    assert {"outbreak_id", "region_id", "onset_date", "onset_definition", "usage"} <= required
    assert schema["primary_region_level"] == "ADM1"


def test_region_week_label_uses_future_window_only() -> None:
    assert p2a.outbreak_label_for_week("2024-01-01", "2024-01-08") == 1
    assert p2a.outbreak_label_for_week("2024-01-01", "2024-01-07") == "excluded_already_in_outbreak"
    assert p2a.outbreak_label_for_week("2024-01-01", "2024-03-20", followup_known_until="2024-05-31") == 0
    assert (
        p2a.outbreak_label_for_week("2024-01-01", "2024-03-20", followup_known_until="2024-01-31")
        == "censored_insufficient_followup"
    )


def test_negative_sampling_excludes_onset_buffer_and_active_outbreak() -> None:
    assert not p2a.is_negative_sample_eligible("2024-01-01", onset_date="2024-01-20")
    assert not p2a.is_negative_sample_eligible(
        "2024-01-15",
        onset_date="2024-01-01",
        outbreak_end="2024-02-28",
    )
    assert p2a.is_negative_sample_eligible(
        "2024-01-01",
        onset_date="2024-03-20",
        followup_known_until="2024-05-31",
    )


def test_p2a_status_blocks_without_train_eligible_onset_data(tmp_path: Path, monkeypatch) -> None:
    root = _project_root(tmp_path, monkeypatch)
    report_dir = root / "data" / "outputs" / "reports"
    report_dir.mkdir(parents=True)
    (report_dir / "p1c_status.json").write_text(
        json.dumps({"outbreak_roles": {"guinea_2021": "external_test_locked"}}),
        encoding="utf-8",
    )
    (report_dir / "filled_template_validation_status.json").write_text(
        json.dumps({"filled_data_present": False, "v3_sft_train_allowed": False}),
        encoding="utf-8",
    )

    result = p2a.generate("status")
    status = json.loads((report_dir / "p2a_feasibility_status.json").read_text(encoding="utf-8"))

    assert result["schema_ready"] is True
    assert status["first_prototype_train_allowed"] is False
    assert "need_explicit_outbreak_event_table_with_onset_dates" in status["blocked_reason"]
    assert status["gates"]["locked_external_test_retained"] is True


def test_p2a_generates_all_required_reports_and_answers(tmp_path: Path, monkeypatch) -> None:
    root = _project_root(tmp_path, monkeypatch)
    report_dir = root / "data" / "outputs" / "reports"
    report_dir.mkdir(parents=True)
    (report_dir / "p1b_download_inventory.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    (report_dir / "p1b_prisma_dataset_links.json").write_text(json.dumps({"candidate_links": []}), encoding="utf-8")
    (report_dir / "p1c_status.json").write_text(
        json.dumps({"outbreak_roles": {"guinea_2021": "external_test_locked"}}),
        encoding="utf-8",
    )
    (report_dir / "filled_template_validation_status.json").write_text(
        json.dumps({"filled_data_present": False, "v3_sft_train_allowed": False}),
        encoding="utf-8",
    )

    p2a.generate("all")
    expected = [
        "outbreak_prediction_task_definition.md",
        "outbreak_data_requirement_report.md",
        "outbreak_literature_data_survey.md",
        "outbreak_model_training_plan.md",
        "p2a_feasibility_status.md",
    ]
    for name in expected:
        assert (report_dir / name).exists()

    combined = "\n".join((report_dir / name).read_text(encoding="utf-8") for name in expected)
    for phrase in [
        "爆发风险预测 vs 趋势预测",
        "训练样本",
        "标签怎么打",
        "负样本怎么采",
        "需要哪些数据",
        "哪些数据已经可以获取",
        "哪些数据需要人工整理",
        "哪些数据需要学校或合作单位提供",
        "当前是否可以训练",
        "blocked_reason",
    ]:
        assert phrase in combined

    assert (root / "data" / "processed" / "outbreak_prediction" / "outbreak_event_table_schema.json").exists()
    assert (root / "data" / "processed" / "outbreak_prediction" / "region_week_panel_schema.json").exists()

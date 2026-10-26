from __future__ import annotations

from pathlib import Path

from src.reports.outbreak_retraining_design import build_outbreak_retraining_design


def test_outbreak_retraining_design_requires_pooled_holdout_and_loo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = build_outbreak_retraining_design()
    answers = result["answers"]
    assert result["training_allowed"] is False
    assert answers["if_west_africa_drc_uganda_available_need_retraining"] is True
    assert result["recommended_design"]["pooled_candidate_v3"] == "recommended_after_gates_pass"
    assert result["recommended_design"]["leave_one_out_validation"] == "recommended"
    assert "Guinea 2021" in answers["guinea_2021_external_test_locked"]

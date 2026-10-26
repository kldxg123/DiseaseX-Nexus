import json
from pathlib import Path

from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.remote.pull_reports import REPORTS
from src.reports.p0c5_final_review import build_and_write, build_context
from src.step.adapter_registry import update_p0c5_final_review


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _metric_block(macro: float, bal: float, up_recall: float, up_precision: float, false_alarm: float | None = None) -> dict:
    metrics = {
        "macro_f1": macro,
        "balanced_accuracy": bal,
        "false_alarm_rate": false_alarm,
        "per_class": {
            LABEL_UP: {"recall": up_recall, "precision": up_precision},
            LABEL_DOWN: {"recall": 0.8, "precision": 0.7},
            LABEL_STABLE: {"recall": 0.9, "precision": 0.9},
        },
    }
    return {"status": "ok", "json_validity_rate": 1.0, "evidence_list_validity_rate": 1.0, "metrics": metrics}


def _seed_p0c5_inputs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    _write_json(tmp_path / "configs/config.yaml", {"step": {"model_registry_path": "data/processed/model_registry.json"}})
    reports = tmp_path / "data/outputs/reports"
    _write_json(
        reports / "baseline_results.json",
        {
            "status": "ok",
            "best_baseline": {"model": "lightgbm", "horizon": 7, "feature_set": "cases_mobility_proxy", "macro_f1": 0.81},
            "horizons": {
                "7d": {
                    "cases_mobility_proxy": {
                        "model": "lightgbm",
                        "metrics": {
                            "macro_f1": 0.81,
                            "balanced_accuracy": 0.78,
                            "per_class": {LABEL_UP: {"recall": 0.40, "precision": 0.50}},
                        },
                    }
                }
            },
        },
    )
    (reports / "backtest_results.md").write_text(
        "| horizon | feature_set | successful_folds | macro_f1_mean | balanced_accuracy_mean | up_recall_mean |\n"
        "|---|---|---:|---:|---:|---:|\n"
        "| 7d | cases_mobility_proxy | 5 | 0.7543 | 0.7288 | 0.2702 |\n",
        encoding="utf-8",
    )
    _write_json(reports / "qwen_lora_eval_metrics.json", {"status": "ok", "test": _metric_block(0.7005, 0.6995, 0.2947, 0.4444)})
    _write_json(
        reports / "qwen_guard_ensemble_eval.json",
        {"status": "ok", "best_non_training_strategy": {"strategy": "case_growth_safety_guard", "metrics": _metric_block(0.7318, 0.7546, 0.4842, 0.4259, 0.0485)["metrics"]}},
    )
    _write_json(
        reports / "qwen_lora_v2_eval_metrics.json",
        {
            "status": "ok",
            "test_original": _metric_block(0.6815, 0.6798, 0.2526, 0.4068, 0.0274),
            "test_v2_prompt_view": _metric_block(0.6743, 0.6699, 0.2842, 0.3462, 0.0399),
        },
    )
    _write_json(
        reports / "qwen_lora_v2_compare.json",
        {
            "status": "ok",
            "comparison_limited": True,
            "comparison_limited_reason": "cross_horizon_country_date_overlap_detected",
            "promote_gate_passed": False,
        },
    )
    _write_json(
        reports / "same_split_baseline_eval.json",
        {"status": "ok", "comparison_limited": True, "comparison_limited_reason": "cross_horizon_country_date_overlap_detected", "mixed_horizon_aggregate": {"metrics": _metric_block(0.6780, 0.6679, 0.2842, 0.3913, 0.0329)["metrics"]}},
    )
    for name in ["p0a_status", "p0a_final_review", "p0c1_status", "p0c2_status", "p0c3_status", "p0c4a_status", "p0c4b_train_status"]:
        _write_json(reports / f"{name}.json", {"status": "ok", "blocked_reason": "none"})
    (reports / "data_quality_report.md").write_text("case_records: 2485\npanel_records: 2485\n", encoding="utf-8")
    (reports / "current_data_inventory.md").write_text("raw_files: 13\nprocessed_files: 20\n", encoding="utf-8")
    _write_json(
        tmp_path / "data/processed/model_registry.json",
        {
            "allow_auto_promote": False,
            "adapters": [
                {"adapter_id": "qwen25_lora_20260623_011633", "status": "candidate", "promote_allowed": False},
                {"adapter_id": "qwen25_lora_v2_20260624_010911", "status": "candidate", "promote_allowed": False},
            ],
        },
    )


def test_p0c5_report_generation_labels_metric_sources(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_p0c5_inputs(tmp_path)

    result = build_and_write("final_model_review")
    payload = json.loads((tmp_path / "data/outputs/reports/final_model_review.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "data/outputs/reports/final_model_review.md").read_text(encoding="utf-8")

    assert result["status"] == "ok"
    assert "P0a LightGBM holdout" in markdown
    assert "P0c-2 Qwen v1 SFT test" in markdown
    assert "P0c-4A v1 + case_growth_safety_guard" in markdown
    assert "P0c-4B Qwen v2 SFT test" in markdown
    assert payload["comparison_limited"] is True
    assert payload["conclusions"]["v2_did_not_improve_up_recall"] is True


def test_p0c5_data_gap_and_next_plan_are_no_immediate_v3(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_p0c5_inputs(tmp_path)

    build_and_write("data_gap_report")
    build_and_write("next_iteration_plan")
    gap = json.loads((tmp_path / "data/outputs/reports/data_gap_report.json").read_text(encoding="utf-8"))
    plan = json.loads((tmp_path / "data/outputs/reports/next_iteration_plan.json").read_text(encoding="utf-8"))

    assert gap["school_data_request_ready"] is True
    assert any("district" in item.lower() for item in gap["main_data_gaps"])
    assert any("DPO" in item or "RLHF" in item for item in gap["main_data_gaps"])
    assert plan["immediate_v3_training_recommended"] is False
    assert plan["recommended_next_action"] == "collect_more_up_hard_cases_before_v3_training"


def test_p0c5_registry_update_keeps_candidate_hold_no_promote(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_p0c5_inputs(tmp_path)
    for report in ["final_model_review", "model_selection_report", "up_recall_failure_report", "data_gap_report", "next_iteration_plan"]:
        build_and_write(report)

    result = update_p0c5_final_review("configs/config.yaml")
    registry = json.loads((tmp_path / "data/processed/model_registry.json").read_text(encoding="utf-8"))
    records = {item["adapter_id"]: item for item in registry["adapters"]}

    assert result["status"] == "ok"
    assert records["qwen25_lora_20260623_011633"]["review_status"] == "hold"
    assert records["qwen25_lora_20260623_011633"]["reason"] == "low_up_recall"
    assert records["qwen25_lora_v2_20260624_010911"]["review_status"] == "hold"
    assert records["qwen25_lora_v2_20260624_010911"]["reason"] == "up_recall_decreased"
    assert records["qwen25_lora_v2_20260624_010911"]["promote_allowed"] is False
    assert records["qwen25_lora_v2_20260624_010911"]["production"] is False
    assert list((tmp_path / "data/processed").glob("model_registry_backup_*.json"))


def test_p0c5_pull_whitelist_contains_final_reports_not_weights():
    for name in [
        "final_model_review.md",
        "final_model_review.json",
        "model_selection_report.md",
        "up_recall_failure_report.json",
        "data_gap_report.md",
        "next_iteration_plan.json",
        "final_project_status.md",
    ]:
        assert name in REPORTS
    assert "models/" not in REPORTS
    assert "lora_weights/" not in REPORTS

import json
from pathlib import Path

from src.eval import qwen_guard_ensemble_eval as guard
from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.eval.p0c4a_common import cross_horizon_leakage, map_sft_to_trend
from src.eval.qwen_prompt_evidence_diagnosis import diagnose
from src.eval.up_recall_slice_analysis import analyze_slices
from src.reports.p0c4a_status import build_status


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _sft_record(country: str, date: str, horizon: int, label: str) -> dict:
    user_payload = {
        "country": country,
        "adm1": "",
        "date": date,
        "horizon_days": horizon,
        "features": {"case_features": {"growth_7d": 0.5, "new_cases_7d_sum": 3}},
    }
    return {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "预测:" + json.dumps(user_payload, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps({"trend": label}, ensure_ascii=False)},
        ]
    }


def _trend_row(country: str, date: str, horizon: int, label: str) -> dict:
    return {
        "country": country,
        "adm1": "",
        "date": date,
        "horizon_days": horizon,
        "label": label,
        "features": {"case_features": {"growth_7d": 0.5, "new_cases_7d_sum": 3}},
    }


def test_mapping_coverage_and_cross_horizon_leakage_reporting(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for split, horizon, label in [("train", 7, LABEL_UP), ("val", 14, LABEL_STABLE), ("test", 21, LABEL_DOWN)]:
        _write_jsonl(tmp_path / f"data/processed/qwen25_sft_{split}.jsonl", [_sft_record("A", "2020-01-01", horizon, label)])
    for horizon, label in [(7, LABEL_UP), (14, LABEL_STABLE), (21, LABEL_DOWN)]:
        _write_jsonl(tmp_path / f"data/processed/trend_dataset_{horizon}d.jsonl", [_trend_row("A", "2020-01-01", horizon, label)])
    _write_jsonl(tmp_path / "data/processed/trend_dataset_30d.jsonl", [])

    mapped, coverage = map_sft_to_trend()
    leakage = cross_horizon_leakage(mapped)

    assert coverage["test"]["coverage"] == 1.0
    assert coverage["test"]["unmapped_count"] == 0
    assert leakage["leakage_check_passed"] is False
    assert leakage["cross_horizon_country_date_overlap_count"] == 1


def test_guard_missing_qwen_artifact_blocks_without_generation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = guard.evaluate_strategies()
    report = guard.write_report(result)

    assert result["status"] == "blocked"
    assert result["missing_artifacts"] == ["qwen_lora_eval_samples.jsonl"]
    assert report.name == "qwen_guard_ensemble_eval.md"
    assert guard.PREDICTIONS_PATH != "data/outputs/reports/qwen_lora_eval_samples.jsonl"


def test_guard_metrics_and_recommendation_rules():
    y_true = [LABEL_UP, LABEL_STABLE, LABEL_STABLE, LABEL_DOWN]
    flags = [True, True, False, False]
    review = guard._flag_metrics(y_true, flags)
    false_alarm = guard._false_alarm_rate(y_true, flags)
    metrics = guard._strategy_metrics("guard", y_true, [LABEL_UP, LABEL_UP, LABEL_STABLE, LABEL_DOWN], {"macro_f1": 0.7, "balanced_accuracy": 0.7})

    assert review["review_recall"] == 1.0
    assert review["review_precision"] == 0.5
    assert false_alarm == 1 / 3
    assert metrics["false_positive_count"] == 1
    assert metrics["stable_to_up_count"] == 1


def test_threshold_tuning_uses_rows_passed_by_validation_only():
    val_rows = [
        {"true_label": LABEL_UP, "probabilities": {LABEL_UP: 0.31}},
        {"true_label": LABEL_STABLE, "probabilities": {LABEL_UP: 0.05}},
    ]
    tuned = guard._tune_baseline_threshold(val_rows)

    assert tuned["threshold"] in {0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60}
    assert "false_alarm_rate" in tuned


def test_up_recall_slice_analysis_summarizes_up_to_stable():
    samples = [
        {
            "sample_id": "s1",
            "true_label": LABEL_UP,
            "predicted_label": LABEL_STABLE,
            "horizon_days": 7,
            "country": "A",
            "date": "2020-02-01",
            "evidence": ["cases increased"],
            "data_quality_flags": ["missing_lat_lon"],
            "input_features": {"case_features": {"growth_7d": 0.5, "new_cases_7d_sum": 9}},
            "confidence": 0.8,
            "risk_score": 0.6,
        }
    ]
    result = analyze_slices(samples)

    assert result["answers"]["qwen_stable_bias_related"] is True
    assert result["up_as_stable_count"] == 1
    assert result["data_quality_flags"]["missing_lat_lon"] == 1


def test_prompt_diagnosis_blocks_when_predictions_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = diagnose()

    assert result["status"] == "blocked"
    assert result["blocked_reason"].startswith("missing_artifacts")


def test_p0c4a_status_keeps_candidate_and_no_promote(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    reports.mkdir(parents=True)
    payloads = {
        "p0c3_final_review.json": {"status": "ok", "blocked_reason": "none"},
        "same_split_baseline_eval.json": {"status": "ok", "blocked_reason": "none", "same_split_baseline_available": True, "comparison_limited": False, "train_mapping_coverage": 1.0, "val_mapping_coverage": 1.0, "test_mapping_coverage": 1.0},
        "up_recall_slice_analysis.json": {"status": "ok", "blocked_reason": "none", "answers": {"main_up_misclassification": "上升 -> 稳定"}},
        "qwen_guard_ensemble_eval.json": {"status": "ok", "blocked_reason": "none", "qwen_only_metrics": {"up_recall": 0.29}, "best_non_training_strategy": {"strategy": "case_growth_safety_guard", "metrics": {"up_recall": 0.5, "macro_f1": 0.6, "up_precision": 0.31, "false_alarm_rate": 0.1}}, "recommended_next_action": "evaluate_guarded_candidate_before_retraining"},
        "qwen_prompt_evidence_diagnosis.json": {"status": "ok", "blocked_reason": "none"},
    }
    for name, payload in payloads.items():
        (reports / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    registry = tmp_path / "data/processed/model_registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"allow_auto_promote": False, "adapters": [{"adapter_id": "qwen25_lora_20260623_011633", "status": "candidate", "promote_allowed": False, "allow_auto_promote": False}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    status = build_status()

    assert status["status"] == "ok"
    assert status["registry_status"] == "candidate"
    assert status["promote_allowed"] is False
    assert status["promote_gate_reached"] is False

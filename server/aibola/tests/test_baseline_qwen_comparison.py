import json
from pathlib import Path

from src.eval.baseline_qwen_comparison import build_comparison
from src.eval.metrics import LABEL_UP


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_comparison_marks_different_metric_sources(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    _write(
        tmp_path / "data/outputs/reports/qwen_lora_eval_metrics.json",
        {
            "status": "ok",
            "test": {
                "json_validity_rate": 1.0,
                "metrics": {
                    "macro_f1": 0.7,
                    "balanced_accuracy": 0.69,
                    "per_class": {LABEL_UP: {"recall": 0.29}},
                },
            },
        },
    )
    _write(
        tmp_path / "data/outputs/reports/baseline_results.json",
        {"best_overall": {"model": "RandomForestClassifier", "feature_set": "cases_weather", "macro_f1": 0.5}},
    )
    result = build_comparison()
    assert result["status"] == "ok"
    assert result["same_split"] is False
    assert result["comparison_limited"] is True
    assert "P0a baseline_results" in result["sources"]["baseline_results"]

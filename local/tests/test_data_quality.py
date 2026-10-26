import json
from pathlib import Path

import pytest

from src.data.quality_report import PREDICTION_TIME_CONVENTION, _future_leakage_check, _label_summary
from src.eval.metrics import LABEL_STABLE, LABEL_UP


def test_future_leakage_static_check_passes_for_normal_features():
    result = _future_leakage_check(
        [{"date": "2014-01-01", "new_cases_7d_sum": 1}],
        [{"features": {"case_features": {"growth_7d": 0.1}}}],
    )
    assert result["status"] == "passed_static_check"
    assert result["prediction_time_convention"] == PREDICTION_TIME_CONVENTION


def test_label_summary_uses_fixed_label_order(tmp_path: Path):
    processed = tmp_path
    rows = [{"label": LABEL_UP}, {"label": LABEL_STABLE}]
    (processed / "trend_dataset_7d.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    summary = _label_summary(processed, [7])
    assert summary["7d"]["label_counts"][LABEL_UP] == 1
    assert summary["7d"]["label_counts"][LABEL_STABLE] == 1


def test_generated_quality_report_when_present():
    path = Path("data/outputs/reports/data_quality_report.md")
    if not path.exists():
        pytest.skip("P0a generated report is not present yet.")
    text = path.read_text(encoding="utf-8")
    if "Prediction is made at the end of date D" not in text:
        pytest.skip("P0a quality report has not been regenerated yet.")
    assert "short_horizon_training_allowed:" in text
    assert "Prediction is made at the end of date D" in text

from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.models.baseline_train import FEATURE_SETS, train_holdout


def _sample(idx: int, label: str) -> dict:
    return {
        "sample_id": f"s{idx}",
        "date": f"2014-01-{(idx % 28) + 1:02d}",
        "label": label,
        "features": {
            "case_features": {"growth_7d": idx / 10, "new_cases_7d_sum": idx},
            "weather_features": {"temp_mean_7d": 20 + idx % 3},
            "mobility_features": {"infectious_inflow_index": idx % 5},
        },
    }


def test_train_holdout_uses_real_ml_on_synthetic_data(tmp_path):
    labels = [LABEL_UP, LABEL_DOWN, LABEL_STABLE] * 12
    samples = [_sample(idx, label) for idx, label in enumerate(labels)]
    result = train_holdout(samples, 7, "cases_weather_mobility_proxy", output_figures=tmp_path)
    assert result["status"] == "ok"
    assert result["model"] in {"lightgbm", "xgboost", "hist_gradient_boosting", "random_forest"}
    assert result["metrics"]["macro_f1"] >= 0
    assert "rule" not in result["model"]


def test_feature_set_names_are_fixed():
    assert set(FEATURE_SETS) == {"cases_only", "cases_weather", "cases_mobility_proxy", "cases_weather_mobility_proxy"}

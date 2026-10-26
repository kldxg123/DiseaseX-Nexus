from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.eval.qwen_error_analysis import analyze_samples, confusion_pairs_from_matrix


def test_confusion_breakdown_finds_up_as_stable():
    matrix = {
        LABEL_UP: {LABEL_UP: 2, LABEL_DOWN: 1, LABEL_STABLE: 5},
        LABEL_DOWN: {LABEL_UP: 0, LABEL_DOWN: 3, LABEL_STABLE: 0},
        LABEL_STABLE: {LABEL_UP: 1, LABEL_DOWN: 0, LABEL_STABLE: 9},
    }
    pairs = confusion_pairs_from_matrix(matrix)
    assert pairs[f"{LABEL_UP}_as_{LABEL_STABLE}"] == 5
    assert pairs[f"{LABEL_UP}_as_{LABEL_DOWN}"] == 1


def test_sample_analysis_counts_json_and_horizon_errors():
    samples = [
        {
            "true_label": LABEL_UP,
            "predicted_label": LABEL_STABLE,
            "horizon_days": 7,
            "country": "A",
            "confidence": 0.9,
            "error_type": f"{LABEL_UP}_as_{LABEL_STABLE}",
            "json_checks": {"valid_json": True, "repaired_json": False, "fallback_json": False, "confidence_valid": True, "risk_score_valid": True},
            "evidence": ["x"],
        },
        {
            "true_label": LABEL_DOWN,
            "predicted_label": LABEL_DOWN,
            "horizon_days": 14,
            "country": "B",
            "confidence": 0.7,
            "error_type": "correct",
            "json_checks": {"valid_json": False, "repaired_json": True, "fallback_json": True, "confidence_valid": True, "risk_score_valid": True},
            "evidence": [],
        },
    ]
    result = analyze_samples(samples)
    assert result["sample_analysis_status"] == "ok"
    assert result["error_count"] == 1
    assert result["json_counts"]["invalid_json_count"] == 1
    assert result["horizon_error_counts"]["7d"][f"{LABEL_UP}_as_{LABEL_STABLE}"] == 1

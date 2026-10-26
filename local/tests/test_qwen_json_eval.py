import json

from src.eval.metrics import LABEL_STABLE, LABEL_UP
from src.models.qwen25_json_eval import evaluate_predictions, ground_truth, prompt_messages


def _record(label: str = LABEL_UP) -> dict:
    return {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "predict future 14 days {\"horizon_days\": 14}"},
            {"role": "assistant", "content": json.dumps({"trend": label, "confidence": 0.7, "risk_score": 0.8, "evidence": ["x"]}, ensure_ascii=False)},
        ]
    }


def test_eval_prompt_excludes_assistant_ground_truth():
    record = _record()
    prompt = prompt_messages(record)
    assert [item["role"] for item in prompt] == ["system", "user"]
    assert "assistant" not in json.dumps(prompt)
    assert ground_truth(record)["trend"] == LABEL_UP


def test_evaluate_predictions_reports_json_and_metrics():
    record = _record(LABEL_STABLE)
    raw = json.dumps({"trend": LABEL_STABLE, "confidence": 0.4, "risk_score": 0.3, "evidence": []}, ensure_ascii=False)
    result = evaluate_predictions([record], [raw])
    assert result["json_validity_rate"] == 1.0
    assert result["metrics"]["macro_f1"] >= 0
    assert "14d" in result["horizon_metrics"]

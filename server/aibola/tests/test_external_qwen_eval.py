from __future__ import annotations

import json

from src.eval.external_qwen_eval import _rows_and_metrics
from src.eval.metrics import LABEL_UP
from src.models.qwen25_json_eval import prompt_messages


def _record() -> dict:
    return {
        "sample_id": "q1",
        "horizon_days": 7,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "predict {\"horizon_days\": 7}"},
            {"role": "assistant", "content": json.dumps({"trend": LABEL_UP, "confidence": 0.8, "risk_score": 0.9, "evidence": ["case growth"]}, ensure_ascii=False)},
        ],
    }


def test_external_qwen_prompt_excludes_ground_truth_and_metrics_parse() -> None:
    record = _record()
    prompt = prompt_messages(record)
    assert [row["role"] for row in prompt] == ["system", "user"]
    rows, metrics = _rows_and_metrics([record], [json.dumps({"trend": LABEL_UP, "confidence": 0.7, "risk_score": 0.6, "evidence": []}, ensure_ascii=False)])
    assert rows[0]["true_label"] == LABEL_UP
    assert metrics["json_validity_rate"] == 1.0
    assert metrics["metrics"]["up_recall"] == 1.0

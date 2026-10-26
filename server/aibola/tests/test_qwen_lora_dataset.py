import json

from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.models.qwen25_lora_dataset import balanced_train_records, label_distribution, record_label


def _record(label: str, idx: int) -> dict:
    return {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": f"future 7 days sample {idx}"},
            {"role": "assistant", "content": json.dumps({"trend": label, "confidence": 0.5, "risk_score": 0.5, "evidence": []}, ensure_ascii=False)},
        ]
    }


def test_balanced_train_oversamples_minorities_only():
    records = [_record(LABEL_UP, i) for i in range(2)]
    records += [_record(LABEL_DOWN, 10 + i) for i in range(3)]
    records += [_record(LABEL_STABLE, 20 + i) for i in range(8)]
    balanced, meta = balanced_train_records(records, max_ratio=2.0, seed=1)
    dist = label_distribution(balanced)
    assert dist[LABEL_UP] == 4
    assert dist[LABEL_DOWN] == 6
    assert dist[LABEL_STABLE] == 8
    assert meta["oversampling_ratio"][LABEL_STABLE] == 1.0
    assert all(record_label(record) in {LABEL_UP, LABEL_DOWN, LABEL_STABLE} for record in balanced)

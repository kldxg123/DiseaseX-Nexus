import json

from src.models.qwen25_sft_validate import validate_split


def _record(date="2014-01-01", trend="上升"):
    return {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": json.dumps({"date": date}, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps({"trend": trend, "confidence": 0.5, "risk_score": 0.6, "evidence": []}, ensure_ascii=False)},
        ]
    }


def test_validate_split_accepts_messages(tmp_path):
    path = tmp_path / "train.jsonl"
    path.write_text(json.dumps(_record(), ensure_ascii=False) + "\n", encoding="utf-8")
    result = validate_split("train", path)
    assert result["records"] == 1
    assert result["error_count"] == 0
    assert result["chrono_verification_method"] == "date/sample_id embedded in input"


def test_validate_split_rejects_bad_assistant_json(tmp_path):
    item = _record()
    item["messages"][-1]["content"] = "not json"
    path = tmp_path / "train.jsonl"
    path.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
    result = validate_split("train", path)
    assert result["error_count"] == 1

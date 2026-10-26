import json
from pathlib import Path

from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.models.qwen25_candidate_v2_dataset_builder import build_candidate_v2_dataset
from src.utils.io import read_jsonl


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _record(country: str, date: str, horizon: int, label: str, growth: float, recent: int = 2) -> dict:
    user_payload = {
        "country": country,
        "adm1": "",
        "date": date,
        "horizon_days": horizon,
        "features": {
            "case_features": {
                "growth_7d": growth,
                "growth_14d": growth,
                "growth_28d": growth,
                "case_acceleration": growth,
                "new_cases_7d_sum": recent,
                "new_cases_14d_sum": recent,
            }
        },
    }
    return {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "输入:" + json.dumps(user_payload, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps({"trend": label, "confidence": 0.8, "risk_score": 0.6, "evidence": []}, ensure_ascii=False)},
        ]
    }


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "configs/train_qwen25_lora_v2.yaml"
    _write_json(
        path,
        {
            "original_train_file": "data/processed/qwen25_sft_train.jsonl",
            "validation_file": "data/processed/qwen25_sft_val.jsonl",
            "test_file": "data/processed/qwen25_sft_test.jsonl",
            "minority_oversample_max_ratio": 2.0,
            "seed": 7,
        },
    )
    return path


def test_v2_dataset_augments_up_without_touching_val_or_test(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _config(tmp_path)
    train = [
        _record("A", "2020-01-01", 7, LABEL_UP, 0.5),
        _record("B", "2020-01-02", 7, LABEL_STABLE, 0.0),
        _record("C", "2020-01-03", 7, LABEL_DOWN, 0.4),
    ]
    val = [_record("V", "2020-02-01", 14, LABEL_UP, 0.3)]
    test = [_record("T", "2020-03-01", 21, LABEL_STABLE, 0.0)]
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_train.jsonl", train)
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_val.jsonl", val)
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_test.jsonl", test)

    result = build_candidate_v2_dataset("configs/train_qwen25_lora_v2.yaml")
    v2 = read_jsonl(tmp_path / "data/processed/qwen25_sft_train_v2.jsonl")

    assert result["status"] == "ok"
    assert result["no_test_leakage"] is True
    assert result["validation_unchanged"] is True
    assert result["test_unchanged"] is True
    assert result["v2_train_distribution"][LABEL_UP] > result["original_train_distribution"][LABEL_UP]
    assert any(row.get("metadata", {}).get("synthetic_or_rewritten") is True for row in v2)
    assert all(row.get("metadata", {}).get("source_reason") != "test" for row in v2 if row.get("metadata"))


def test_v2_dataset_blocks_exact_test_leakage(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _config(tmp_path)
    leaked = _record("A", "2020-01-01", 7, LABEL_UP, 0.5)
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_train.jsonl", [leaked])
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_val.jsonl", [_record("V", "2020-02-01", 14, LABEL_STABLE, 0.0)])
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_test.jsonl", [leaked])

    result = build_candidate_v2_dataset("configs/train_qwen25_lora_v2.yaml")

    assert result["status"] == "blocked"
    assert result["no_test_leakage"] is False
    assert result["blocked_reason"] == "test_leakage_detected"

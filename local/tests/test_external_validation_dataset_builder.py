from __future__ import annotations

import json
from pathlib import Path

from src.eval.external_validation_dataset_builder import build_external_validation_dataset
from src.utils.io import read_jsonl


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _sample(sample_id: str, usage: str, label: str = "上升") -> dict:
    return {
        "sample_id": sample_id,
        "date": "2021-02-01",
        "country": "Guinea",
        "adm1": "N'Zerekore",
        "adm2": "",
        "label": label,
        "horizon_days": 7,
        "usage": usage,
        "features": {"case_features": {"growth_7d": 1.0, "new_cases_7d_sum": 3}},
        "data_quality_flags": [],
    }


def test_external_validation_dataset_filters_external_role_and_30d_not_available(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    processed = tmp_path / "data/processed"
    _write_jsonl(
        processed / "trend_dataset_external_7d.jsonl",
        [_sample("a", "external_test_candidate"), _sample("b", "validation_candidate", "稳定")],
    )
    for horizon in [14, 21, 30]:
        _write_jsonl(processed / f"trend_dataset_external_{horizon}d.jsonl", [])
    result = build_external_validation_dataset(processed)
    assert result["status"] == "ok"
    assert result["training_allowed"] is False
    assert result["horizons"]["7d"]["external_test_samples"] == 1
    assert result["horizons"]["30d"]["status"] == "not_available"
    rows = read_jsonl(tmp_path / "data/processed/external_validation/external_test_7d.jsonl")
    assert [row["sample_id"] for row in rows] == ["a"]
    qwen_rows = read_jsonl(tmp_path / "data/processed/external_validation/external_qwen_sft_test_7d.jsonl")
    assert qwen_rows and [msg["role"] for msg in qwen_rows[0]["messages"]] == ["system", "user", "assistant"]

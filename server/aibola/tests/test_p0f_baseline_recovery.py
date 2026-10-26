from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.eval.baseline_artifact_recovery import recover_baseline_artifacts


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_baseline_recovery_never_uses_external_test_for_training(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("joblib")
    monkeypatch.chdir(tmp_path)
    _write_json(
        tmp_path / "configs/config.yaml",
        {
            "project": {"random_seed": 7},
            "paths": {"data_processed": "data/processed", "outputs": "data/outputs"},
        },
    )
    processed = tmp_path / "data/processed"
    processed.mkdir(parents=True)
    labels = ["上升", "下降", "稳定"] * 5
    for horizon in [7, 14, 21, 30]:
        rows = []
        for idx, label in enumerate(labels):
            rows.append(
                {
                    "sample_id": f"{horizon}_{idx}",
                    "date": f"2020-01-{idx + 1:02d}",
                    "label": label,
                    "features": {"case_features": {"new_cases": idx, "case_growth": idx % 3}},
                }
            )
        (processed / f"trend_dataset_{horizon}d.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
    result = recover_baseline_artifacts("configs/config.yaml")
    assert result["status"] == "ok"
    assert result["external_test_used_for_training"] is False
    assert result["external_test_used_for_threshold_tuning"] is False
    assert result["baseline_recovery_status"] == "rebuilt"

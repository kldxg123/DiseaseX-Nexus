from __future__ import annotations

import json
from pathlib import Path

from src.data.external_label_builder import build_external_labels
from src.processors.label_builder import PREDICTION_TIME_CONVENTION


def test_external_label_builder_preserves_prediction_time_convention(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    processed = tmp_path / "data/processed"
    processed.mkdir(parents=True)
    rows = []
    for idx in range(25):
        rows.append(
            {
                "date": f"2021-01-{idx + 1:02d}",
                "country": "Guinea",
                "adm1": "Nzerekore",
                "adm2": "",
                "new_cases": 1 if idx < 15 else 5,
                "usage": "external_test_candidate",
                "data_quality_flags": [],
            }
        )
    (processed / "ebola_quant_panel_external_v1.parquet.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "paths": {"data_processed": str(processed), "outputs": str(tmp_path / "data/outputs")},
                "data": {"horizons": [7], "past_window_days": 7, "up_threshold": 0.2, "down_threshold": -0.2},
            }
        ),
        encoding="utf-8",
    )
    result = build_external_labels(str(cfg), [7])
    assert result["status"] == "ok"
    assert result["prediction_time_convention"] == PREDICTION_TIME_CONVENTION
    assert Path("data/processed/trend_dataset_external_7d.jsonl").exists()

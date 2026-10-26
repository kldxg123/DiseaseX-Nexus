from __future__ import annotations

import json
from pathlib import Path

from src.data.manual_upload_validator import validate_health_zone_cases
from src.eval.guard_after_data_expansion import chronological_splits, tune_on_validation
from src.processors.external_feature_builder import build_external_features
from src.utils.io import read_table


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _write_config(root: Path) -> Path:
    cfg = {
        "project": {"random_seed": 42},
        "paths": {
            "data_raw": str(root / "data/raw"),
            "data_processed": str(root / "data/processed"),
            "outputs": str(root / "data/outputs"),
        },
        "data": {"horizons": [7], "past_window_days": 14, "up_threshold": 0.2, "down_threshold": -0.2},
    }
    path = root / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_manual_health_zone_upload_blocks_negative_and_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "health_zone_cases.csv"
    path.write_text(
        "date,country,adm1,adm2,health_zone,new_cases,cum_cases,deaths,source,reporting_status,quality_note\n"
        "2014-01-01,Guinea,A,B,Z,-1,10,1,test,confirmed,\n"
        "2014-01-01,Guinea,A,B,Z,2,12,1,test,confirmed,\n",
        encoding="utf-8",
    )
    result = validate_health_zone_cases(path)
    assert result["status"] == "blocked"
    assert result["negative_new_cases"] == 1
    assert result["duplicate_count"] == 1


def test_external_feature_builder_merges_without_future_labels(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    processed = tmp_path / "data/processed"
    panel = [
        {
            "date": "2014-01-01",
            "country": "Guinea",
            "adm1": "",
            "adm2": "",
            "new_cases_7d_sum": 10,
            "new_cases_14d_sum": 20,
            "growth_7d": 0.5,
            "growth_14d": 0.2,
            "precipitation_sum_7d": 3,
            "precipitation_sum_14d": 5,
            "precipitation_sum_30d": 8,
            "data_quality_flags": ["country_centroid_lat_lon_proxy", "population_proxy_default_1m"],
        }
    ]
    region = [
        {
            "country": "Guinea",
            "adm1": "",
            "adm2": "",
            "population": 1_000_000,
            "population_density": 50,
            "road_accessibility_index": 0.8,
            "data_quality_flags": [],
            "source": "fixture_region_metadata",
        }
    ]
    _write_jsonl(processed / "ebola_quant_panel.parquet.jsonl", panel)
    _write_jsonl(processed / "region_metadata.parquet.jsonl", region)
    result = build_external_features(str(config))
    assert result["status"] == "ok"
    merged = read_table(processed / "ebola_quant_panel.parquet")
    assert "population_normalized_incidence_7d" in merged[0]
    assert "case_growth_x_road_access" in merged[0]
    assert result["no_future_leakage"] is True
    assert result["no_test_leakage"] is True


def test_guard_tuning_uses_validation_split_shape() -> None:
    samples = []
    for idx in range(30):
        label = "上升" if idx in {18, 19, 25, 26} else "稳定"
        samples.append(
            {
                "sample_id": f"s{idx}",
                "date": f"2014-01-{idx + 1:02d}",
                "label": label,
                "features": {
                    "case_features": {"growth_7d": 1.0 if label == "上升" else 0.0, "new_cases_7d_sum": 5},
                    "external_features": {"population_normalized_incidence_7d": 1.0 if label == "上升" else 0.0},
                    "mobility_features": {"cross_border_accessibility_index": 1.0, "road_density_km_per_area": 10.0},
                    "weather_features": {"rainfall_lag_7": 0.0},
                },
            }
        )
    train, val, test = chronological_splits(samples)
    assert len(train) == 18
    assert len(val) == 6
    assert len(test) == 6
    tuned = tune_on_validation(val)
    assert "params" in tuned
    assert tuned["validation_metrics"]["up_recall"] >= 0.0

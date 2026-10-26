from __future__ import annotations

import csv
import json
from pathlib import Path


REP = Path("USER_HOME/projects/openclaw/reports/r4d_r1_origin_safe_20260806T_R1")


def read_csv(name: str) -> list[dict[str, str]]:
    with (REP / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def is_origin_safe(row: dict[str, str]) -> bool:
    return (
        row["feature_cutoff_assertion"] == "True"
        and row["label_availability_assertion"] == "True"
        and row["preprocessing_cutoff_assertion"] == "True"
        and row["target_alignment_assertion"] == "True"
        and row["overall_origin_safe"] == "True"
    )


def test_dataverse_week_not_state_and_dates() -> None:
    data = json.loads((REP / "R4D_R1_DATAVERSE_MAPPING.json").read_text())
    profile = data["dataverse_profile"]
    assert profile["week_is_state_column"] is False
    assert profile["state_count"] == 37
    assert profile["has_1970"] is False
    assert profile["date_min"] == "2009-10-04"
    assert profile["date_max"] == "2017-05-14"


def test_dataverse_author_subset_relationship() -> None:
    data = json.loads((REP / "R4D_R1_DATAVERSE_MAPPING.json").read_text())
    assert data["content_relationship"] == "AUTHOR_SUBSET_OF_DATAVERSE"
    assert data["overlap_key_count"] == 12765
    assert data["dataverse_only_weeks"] == 53
    assert data["author_only_weeks"] == 0
    assert data["max_abs_difference"] <= 1e-8


def test_temporal_worked_example() -> None:
    temporal = json.loads((REP / "R4D_R1_TEMPORAL_SEMANTICS.json").read_text())
    ex = temporal["worked_example"]
    assert ex["sample_anchor_week"] == "2016-04-03"
    assert ex["latest_input_week"] == "2016-03-27"
    assert ex["information_cutoff"] == "2016-03-27"
    assert ex["input_window_start"] == "2015-04-05"
    assert ex["target_week"] == "2016-04-24"


def test_origin_audit_and_leak_guard() -> None:
    rows = read_csv("R4D_R1_ORIGIN_AUDIT.csv")
    assert len(rows) == 3
    assert all(is_origin_safe(row) for row in rows)
    leaked = dict(rows[0])
    leaked["label_availability_assertion"] = "False"
    leaked["overall_origin_safe"] = "False"
    assert not is_origin_safe(leaked)


def test_track_separation_and_author_join_counts() -> None:
    track_a = read_csv("R4D_R1_TRACK_A_AUTHOR_REPLAY_PREDICTIONS.csv")
    track_b = read_csv("R4D_R1_TRACK_B_ORIGIN_SAFE_PREDICTIONS.csv")
    assert {row["track"] for row in track_a} == {"A_AUTHOR_PROTOCOL_REPLAY"}
    assert {row["track"] for row in track_b} == {"B_ORIGIN_SAFE_BACKTEST"}
    comp = json.loads((REP / "R4D_R1_TRACK_A_AUTHOR_COMPARISON.json").read_text())
    assert comp["matched_count"] == 2
    assert len(comp["requested_keys"]) == 3
    assert len(comp["unmatched_keys"]) == 1


def test_metric_recompute_from_predictions() -> None:
    rows = read_csv("R4D_R1_TRACK_B_ORIGIN_SAFE_PREDICTIONS.csv")
    metrics = json.loads((REP / "R4D_R1_TRACK_B_METRICS.json").read_text())
    y_true = [float(row["y_true"]) for row in rows]
    y_pred = [float(row["y_pred_gru"]) for row in rows]
    mae = sum(abs(a - b) for a, b in zip(y_true, y_pred, strict=True)) / len(rows)
    assert abs(mae - metrics["gru"]["mae"]) < 1e-12


def test_fresh_process_replay_keys() -> None:
    repro = json.loads((REP / "R4D_R1_REPRODUCIBILITY.json").read_text())
    assert repro["track_b_fresh_process_rows"] == 3
    assert repro["track_b_keys_match"] is True
    assert repro["track_a_fresh_process_rows"] == 3

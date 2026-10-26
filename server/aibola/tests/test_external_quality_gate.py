from __future__ import annotations

import json
from pathlib import Path

from src.data.external_quality_gate import run_external_quality_gate


def _write_report(root: Path, name: str, payload: dict) -> None:
    path = root / "data/outputs/reports" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_quality_gate_blocks_low_admin_coverage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ok = {"status": "ok", "blocked_reason": "none"}
    _write_report(tmp_path, "external_hdx_column_mapping_report.json", {**ok, "needs_manual_review_count": 0, "usage_counts": {"external_test_candidate": 1}})
    _write_report(tmp_path, "external_admin_matching_report.json", {**ok, "admin_match_coverage": 0.5})
    _write_report(tmp_path, "external_case_normalization_report.json", ok)
    _write_report(tmp_path, "external_case_deduplication_report.json", {**ok, "additive_duplicate_count": 0, "usage_counts": {"external_test_candidate": 1}})
    _write_report(tmp_path, "external_weather_alignment_report.json", ok)
    _write_report(tmp_path, "external_panel_build_report.json", {**ok, "original_panel_unchanged": True, "usage_counts": {"external_test_candidate": 1}})
    _write_report(tmp_path, "external_label_quality_report.json", {**ok, "future_leakage_detected": False})
    result = run_external_quality_gate()
    assert result["status"] == "blocked"
    assert "admin_match_coverage_below_90pct" in result["blocked_reason"]

from __future__ import annotations

import json
from pathlib import Path

from src.reports.train_blocking_resolution_matrix import build_resolution_matrix


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_resolution_matrix_preserves_non_train_roles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write_json(reports / "p1b_download_inventory.json", {"status": "ok", "downloaded_sources": ["NASA POWER", "geoBoundaries"], "summary": {}})
    _write_json(
        reports / "p1b_source_download_report.json",
        {
            "records": [
                {"scope": "drc_2018_2020", "source": "HDX CKAN", "status": "downloaded", "reason": "metadata_only"},
                {"scope": "uganda_2022", "source": "HDX CKAN", "status": "downloaded", "reason": "metadata_only"},
            ]
        },
    )
    _write_json(reports / "external_v3_admin_matching_report.json", {"train_candidate_admin_match_coverage": 0.0})
    _write_json(reports / "external_v3_case_normalization_report.json", {"source_scope_counts": {"drc_2018_2020": 10}})
    _write_json(reports / "external_v3_deduplication_report.json", {"training_eligible_post_dedup": 0, "duplicate_with_original_count": 5})
    _write_json(reports / "external_v3_role_assignment_report.json", {"role_counts": {"cross_check_only": 10, "external_test_locked": 2}})
    _write_json(reports / "v3_retraining_readiness_report.json", {"external_test_retained": True, "blocked_reason": "need_data"})

    result = build_resolution_matrix()
    by_source = {item["data_source"]: item for item in result["matrix"]}
    assert result["training_allowed"] is False
    assert by_source["DRC 2018-2020"]["is_trainable"] is False
    assert by_source["Uganda 2022"]["is_trainable"] is False
    assert by_source["Guinea 2021"]["current_role"] == "external_test_locked"
    assert by_source["NASA POWER"]["can_be_background_feature"] is True

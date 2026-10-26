from __future__ import annotations

import json
from pathlib import Path

from src.data.external_v3_role_assignment import build_external_v3_role_assignment
from src.data_acquisition.source_registry import validate_source_registry
from src.reports.v3_retraining_readiness import build_readiness_report
from src.utils.io import write_jsonl


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_source_registry_validation_blocks_missing_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "registry.yaml"
    path.write_text(
        "outbreak_scopes:\n"
        "  guinea_2021:\n"
        "    countries: [GIN]\n"
        "    date_start: '2021-02-01'\n"
        "    date_end: '2021-06-30'\n"
        "    target_role: external_test_locked\n"
        "sources:\n"
        "  bad_source:\n"
        "    group: case_sources\n"
        "    source_type: direct_url\n",
        encoding="utf-8",
    )
    result = validate_source_registry(path)
    assert result["status"] == "blocked"
    assert any(item["problem"].startswith("missing_") for item in result["problems"])


def test_role_assignment_keeps_guinea_locked_external_test(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / "data/outputs/reports/external_v3_label_quality_report.json", {"status": "ok", "horizons": {}})
    write_jsonl(
        tmp_path / "data/processed/trend_dataset_external_v3_7d.jsonl",
        [
            {
                "sample_id": "g1",
                "source_scope": "guinea_2021",
                "usage": "train_candidate",
                "training_eligible_post_dedup": True,
                "label": "上升",
            },
            {
                "sample_id": "u1",
                "source_scope": "uganda_2022",
                "usage": "train_candidate",
                "training_eligible_post_dedup": True,
                "label": "上升",
            },
        ],
    )
    for horizon in [14, 21, 30]:
        write_jsonl(tmp_path / f"data/processed/trend_dataset_external_v3_{horizon}d.jsonl", [])
    result = build_external_v3_role_assignment()
    assert result["role_counts"]["external_test_locked"] == 1
    assert result["role_counts"]["train_candidate"] == 1
    assert result["external_test_retained"] is True


def test_v3_readiness_uses_external_v3_role_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write_json(
        reports / "external_v3_role_assignment_report.json",
        {
            "status": "ok",
            "role_counts": {"train_candidate": 10, "external_test_locked": 2},
            "outbreak_counts": {"uganda_2022": 10, "guinea_2021": 2},
            "trainable_outbreaks": ["uganda_2022"],
            "new_trainable_sample_count": 10,
            "new_trainable_up_sample_count": 4,
            "hard_positive_count": 4,
            "hard_stable_decline_count": 6,
            "external_test_retained": True,
            "per_horizon": {},
            "label_distribution_by_role": {},
        },
    )
    _write_json(reports / "external_v3_label_quality_report.json", {"future_leakage_detected": False})
    _write_json(reports / "external_v3_admin_matching_report.json", {"train_candidate_admin_match_coverage": 1.0})
    _write_json(reports / "external_v3_column_mapping_report.json", {"semantic_unclear_count": 0})
    _write_json(reports / "external_v3_deduplication_report.json", {"records": 10, "duplicate_with_original_count": 0})
    _write_json(reports / "external_v3_panel_build_report.json", {"records": 10})
    result = build_readiness_report()
    assert result["readiness_source"] == "external_v3_role_assignment"
    assert result["v3_train_allowed"] is False
    assert result["new_trainable_sample_count"] == 10
    assert result["external_test_retained"] is True
    assert "need_at_least_2_train_eligible_external_outbreaks" in result["blocked_reason"]

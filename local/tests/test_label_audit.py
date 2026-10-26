from __future__ import annotations

from datetime import date

from common.db import get_session, init_db
from common.models import ModelFeature
from data_quality_agent.label_audit import audit_model_feature_labels


def test_label_audit_flags_leakage_keys_without_returning_feature_json(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'labels.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        session.add(
            ModelFeature(
                country="United States",
                admin1=None,
                disease="norovirus",
                target_date=date(2025, 1, 13),
                iso_week="2025-W02",
                horizon_days=7,
                feature_json={"current_week_cases": 2, "future_cases": 99, "nested": {"target_flag": True}},
                target_value=4.0,
                target_outbreak_label=True,
                split_type="train",
            )
        )
        session.flush()
        audit = audit_model_feature_labels(session, country="United States", disease="norovirus")

    assert audit.checked_features == 1
    assert audit.horizon_days_checked == [7]
    assert audit.leakage_warnings
    assert audit.summary["leakage_suspect_row_count"] == 1
    rendered = str(audit)
    assert "feature_json" not in rendered
    assert "future_cases" in rendered


def test_label_audit_handles_unsupported_horizon_and_bad_iso_week(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'bad-labels.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        session.add(
            ModelFeature(
                country="United States",
                admin1=None,
                disease="norovirus",
                target_date=date(2025, 1, 1),
                iso_week="not-a-week",
                horizon_days=5,
                feature_json={"current_week_cases": 2},
                target_value=4.0,
                target_outbreak_label=False,
                split_type="train",
            )
        )
        session.flush()
        audit = audit_model_feature_labels(session)

    issues = {row["issue"] for row in audit.inconsistent_rows}
    assert "unsupported_horizon_days" in issues
    assert "invalid_iso_week" in issues
    assert audit.summary["requires_human_review"] is True

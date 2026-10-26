from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cleaner_agent.time_series import iso_week_start_date
from common.models import ModelFeature

ALLOWED_HORIZON_DAYS = {7, 14, 28, 56}
LEAKAGE_KEY_MARKERS = ("future", "next_", "target", "label")


@dataclass
class LabelAuditResult:
    checked_features: int
    horizon_days_checked: list[int]
    leakage_warnings: list[str]
    missing_target_warnings: list[str]
    inconsistent_rows: list[dict[str, Any]]
    summary: dict[str, Any]


def audit_model_feature_labels(
    session: Session,
    *,
    country: str | None = None,
    disease: str | None = None,
    max_examples: int = 20,
) -> LabelAuditResult:
    statement = select(ModelFeature).order_by(
        ModelFeature.country,
        ModelFeature.disease,
        ModelFeature.horizon_days,
        ModelFeature.target_date,
        ModelFeature.iso_week,
    )
    if country is not None:
        statement = statement.where(ModelFeature.country == country)
    if disease is not None:
        statement = statement.where(ModelFeature.disease == disease)
    rows = list(session.scalars(statement).all())

    leakage_warnings: list[str] = []
    missing_target_warnings: list[str] = []
    inconsistent_rows: list[dict[str, Any]] = []
    horizon_days_checked = sorted({int(row.horizon_days) for row in rows if row.horizon_days is not None})

    leakage_rows = 0
    unsupported_horizons = 0
    date_issues = 0
    missing_values = 0
    missing_labels = 0

    for row in rows:
        row_issue_count = 0
        if int(row.horizon_days or 0) not in ALLOWED_HORIZON_DAYS:
            unsupported_horizons += 1
            row_issue_count += 1
            _append_issue(inconsistent_rows, row, "unsupported_horizon_days", max_examples)

        date_issue = _target_date_issue(row)
        if date_issue:
            date_issues += 1
            row_issue_count += 1
            _append_issue(inconsistent_rows, row, date_issue, max_examples)

        if row.target_value is None:
            missing_values += 1
            row_issue_count += 1
            _append_issue(inconsistent_rows, row, "missing_target_value", max_examples)

        if row.target_outbreak_label is None:
            missing_labels += 1
            row_issue_count += 1
            _append_issue(inconsistent_rows, row, "missing_target_outbreak_label", max_examples)

        leakage_keys = _find_leakage_keys(row.feature_json)
        if leakage_keys:
            leakage_rows += 1
            row_issue_count += 1
            _append_issue(inconsistent_rows, row, "feature_key_possible_target_leakage", max_examples)
            if len(leakage_warnings) < max_examples:
                leakage_warnings.append(
                    f"model_feature id={row.id} has leakage-like feature keys: {', '.join(leakage_keys[:5])}"
                )

        if row_issue_count == 0:
            continue

    if missing_values:
        missing_target_warnings.append(f"{missing_values} model_features rows are missing target_value.")
    if missing_labels:
        missing_target_warnings.append(
            f"{missing_labels} model_features rows are missing target_outbreak_label."
        )
    if date_issues:
        missing_target_warnings.append(
            "some model_features rows have target_date/iso_week timing issues; incomplete future windows may need review."
        )

    return LabelAuditResult(
        checked_features=len(rows),
        horizon_days_checked=horizon_days_checked,
        leakage_warnings=leakage_warnings,
        missing_target_warnings=missing_target_warnings,
        inconsistent_rows=inconsistent_rows[: max(0, int(max_examples))],
        summary={
            "checked_features": len(rows),
            "unsupported_horizon_count": unsupported_horizons,
            "timing_issue_count": date_issues,
            "missing_target_value_count": missing_values,
            "missing_target_label_count": missing_labels,
            "leakage_suspect_row_count": leakage_rows,
            "requires_human_review": bool(
                unsupported_horizons or date_issues or missing_values or missing_labels or leakage_rows
            ),
            "limitations": (
                "Rule-based label audit only. It checks metadata and feature-key patterns; "
                "it does not guarantee label correctness."
            ),
        },
    )


def _target_date_issue(row: ModelFeature) -> str | None:
    if not row.target_date or not row.iso_week:
        return "missing_target_date_or_iso_week"
    try:
        week_start = iso_week_start_date(str(row.iso_week))
    except ValueError:
        return "invalid_iso_week"
    if row.target_date < week_start:
        return "target_date_before_feature_week"
    horizon_days = int(row.horizon_days or 0)
    if horizon_days > 0 and row.target_date > week_start + timedelta(days=horizon_days + 14):
        return "target_date_far_beyond_horizon"
    return None


def _find_leakage_keys(value: Any, *, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            dotted = f"{prefix}.{key_text}" if prefix else key_text
            lowered = key_text.lower()
            if any(marker in lowered for marker in LEAKAGE_KEY_MARKERS):
                keys.append(dotted)
            keys.extend(_find_leakage_keys(item, prefix=dotted))
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            keys.extend(_find_leakage_keys(item, prefix=f"{prefix}[{index}]"))
    return sorted(set(keys))


def _append_issue(
    rows: list[dict[str, Any]],
    row: ModelFeature,
    issue: str,
    max_examples: int,
) -> None:
    if len(rows) >= max(0, int(max_examples)):
        return
    rows.append(
        {
            "id": row.id,
            "country": row.country,
            "disease": row.disease,
            "iso_week": row.iso_week,
            "horizon_days": row.horizon_days,
            "issue": issue,
        }
    )

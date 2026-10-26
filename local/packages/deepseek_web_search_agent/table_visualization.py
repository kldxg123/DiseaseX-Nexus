from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem
from deepseek_web_search_agent.table_response_parser import NUMERIC_FIELDS


def build_table_visualization_data(
    rows: list[dict[str, Any]],
    sources: list[SourceEvidenceItem] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_lookup = {_source_id(source): _source_payload(source) for source in sources or []}
    cleaned_rows = [dict(row) for row in rows if isinstance(row, dict)]
    time_series = _time_series(cleaned_rows)
    country_region = _country_region_summary(cleaned_rows)
    source_contribution = _source_contribution_summary(cleaned_rows, source_lookup)
    metric_totals = _metric_totals(cleaned_rows)
    optional_3d = {
        "status": "not_available",
        "point_count": 0,
        "warnings": ["No coordinate fields are present; coordinates are not fabricated."],
    }
    summary = {
        "status": "ok",
        "rows_total": len(cleaned_rows),
        "time_points": len(time_series),
        "country_region_groups": len(country_region),
        "source_count": len(source_contribution),
        "metrics": metric_totals,
        "has_coordinates": False,
        "limitations": "Visualization is based only on cleaned staging rows; no coordinates are fabricated.",
    }
    return {
        "visualization_summary": summary,
        "time_series_data": time_series,
        "country_region_summary": country_region,
        "source_contribution_summary": source_contribution,
        "metric_totals_summary": metric_totals,
        "optional_3d_visualization_data": optional_3d,
    }


def write_table_visualization_files(
    output_dir: str | Path,
    rows: list[dict[str, Any]],
    sources: list[SourceEvidenceItem] | list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    data = build_table_visualization_data(rows, sources)
    paths = {
        "visualization_summary": root / "visualization_summary.json",
        "time_series_data": root / "time_series_data.json",
        "country_region_summary": root / "country_region_summary.json",
        "source_contribution_summary": root / "source_contribution_summary.json",
        "metric_totals_summary": root / "metric_totals_summary.json",
        "optional_3d_visualization_data": root / "optional_3d_visualization_data.json",
    }
    for key, path in paths.items():
        path.write_text(json.dumps(data[key], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def _time_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        date_value = str(row.get("date") or "unknown")
        target = grouped.setdefault(date_value, {"date": date_value, **{field: 0 for field in NUMERIC_FIELDS}, "row_count": 0})
        target["row_count"] += 1
        for field in NUMERIC_FIELDS:
            target[field] += _int_or_zero(row.get(field))
    return [grouped[key] for key in sorted(grouped)]


def _country_region_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("country") or "unknown"), str(row.get("region") or "unknown"))
        target = grouped.setdefault(
            key,
            {"country": key[0], "region": key[1], **{field: 0 for field in NUMERIC_FIELDS}, "row_count": 0},
        )
        target["row_count"] += 1
        for field in NUMERIC_FIELDS:
            target[field] += _int_or_zero(row.get(field))
    return sorted(grouped.values(), key=lambda item: (item["country"], item["region"]))


def _source_contribution_summary(rows: list[dict[str, Any]], sources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = str(row.get("source_id") or "unknown")
        source = sources.get(source_id, {})
        target = grouped.setdefault(
            source_id,
            {
                "source_id": source_id,
                "source_domain": source.get("domain") or row.get("source_domain"),
                "source_url_summary": source.get("url_safe_summary") or row.get("source_url_summary"),
                "source_type": source.get("source_type") or row.get("source_type"),
                "row_count": 0,
                **{field: 0 for field in NUMERIC_FIELDS},
            },
        )
        target["row_count"] += 1
        for field in NUMERIC_FIELDS:
            target[field] += _int_or_zero(row.get(field))
    return sorted(grouped.values(), key=lambda item: str(item["source_id"]))


def _metric_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals = {field: 0 for field in NUMERIC_FIELDS}
    for row in rows:
        for field in NUMERIC_FIELDS:
            totals[field] += _int_or_zero(row.get(field))
    return totals


def _source_id(source: SourceEvidenceItem | dict[str, Any]) -> str:
    if isinstance(source, SourceEvidenceItem):
        return source.source_id
    return str(source.get("source_id") or "")


def _source_payload(source: SourceEvidenceItem | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, SourceEvidenceItem):
        return source.to_dict()
    return dict(source)


def _int_or_zero(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = ["build_table_visualization_data", "write_table_visualization_files"]

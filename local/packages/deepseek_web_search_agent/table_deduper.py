from __future__ import annotations

from collections import defaultdict
from typing import Any


def find_duplicate_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    exact: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    by_place_date: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        exact[_exact_key(row)].append(str(row.get("row_id")))
        by_place_date[_place_date_key(row)].append(row)
    for key, row_ids in exact.items():
        if len(row_ids) > 1:
            groups.append({"duplicate_type": "exact_duplicate", "key": list(key), "row_ids": row_ids})
    for key, group_rows in by_place_date.items():
        if len(group_rows) <= 1:
            continue
        signatures = {_value_signature(row) for row in group_rows}
        if len(signatures) > 1:
            groups.append(
                {
                    "duplicate_type": "conflicting_values_same_date_region",
                    "key": list(key),
                    "row_ids": [str(row.get("row_id")) for row in group_rows],
                }
            )
        elif not any(str(row.get("row_id")) in set(sum([g["row_ids"] for g in groups if g["duplicate_type"] == "exact_duplicate"], [])) for row in group_rows):
            groups.append(
                {
                    "duplicate_type": "possible_duplicate",
                    "key": list(key),
                    "row_ids": [str(row.get("row_id")) for row in group_rows],
                }
            )
    return groups


def _exact_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("date"),
        _norm(row.get("country")),
        _norm(row.get("region")),
        row.get("source_id"),
        row.get("confirmed_cases"),
        row.get("confirmed_deaths"),
        row.get("suspected_cases"),
    )


def _place_date_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("date"), _norm(row.get("country")), _norm(row.get("region")))


def _value_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("confirmed_cases"),
        row.get("confirmed_deaths"),
        row.get("suspected_cases"),
        row.get("contacts_under_monitoring"),
    )


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())

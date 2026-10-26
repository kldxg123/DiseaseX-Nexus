from __future__ import annotations

from deepseek_web_search_agent.table_deduper import find_duplicate_event_rows


def test_table_deduper_detects_exact_and_conflicting_rows() -> None:
    rows = [
        {"row_id": "A", "date": "2026-05-01", "country": "X", "region": "Y", "source_id": "SRC1", "confirmed_cases": 1, "confirmed_deaths": 0, "suspected_cases": 0, "contacts_under_monitoring": 0},
        {"row_id": "B", "date": "2026-05-01", "country": "X", "region": "Y", "source_id": "SRC1", "confirmed_cases": 1, "confirmed_deaths": 0, "suspected_cases": 0, "contacts_under_monitoring": 0},
        {"row_id": "C", "date": "2026-05-02", "country": "X", "region": "Y", "source_id": "SRC2", "confirmed_cases": 1, "confirmed_deaths": 0, "suspected_cases": 0, "contacts_under_monitoring": 0},
        {"row_id": "D", "date": "2026-05-02", "country": "X", "region": "Y", "source_id": "SRC2", "confirmed_cases": 3, "confirmed_deaths": 0, "suspected_cases": 0, "contacts_under_monitoring": 0},
    ]

    groups = find_duplicate_event_rows(rows)
    duplicate_types = {group["duplicate_type"] for group in groups}

    assert "exact_duplicate" in duplicate_types
    assert "conflicting_values_same_date_region" in duplicate_types

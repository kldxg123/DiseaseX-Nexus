from __future__ import annotations

import json

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import mock_source_pack
from deepseek_web_search_agent.table_visualization import build_table_visualization_data, write_table_visualization_files


def _rows():
    return [
        {"date": "2026-05-01", "country": "South Africa", "region": "Gauteng", "confirmed_cases": 2, "confirmed_deaths": 1, "suspected_cases": 0, "contacts_under_monitoring": 62, "source_id": "SRC1"},
        {"date": "2026-05-08", "country": "South Africa", "region": "Gauteng", "confirmed_cases": 3, "confirmed_deaths": 0, "suspected_cases": 1, "contacts_under_monitoring": 20, "source_id": "SRC1"},
    ]


def test_table_visualization_summarizes_metrics_without_coordinates(tmp_path) -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    data = build_table_visualization_data(_rows(), mock_source_pack(request))

    assert data["visualization_summary"]["rows_total"] == 2
    assert data["metric_totals_summary"]["confirmed_cases"] == 5
    assert data["optional_3d_visualization_data"]["status"] == "not_available"

    paths = write_table_visualization_files(tmp_path, _rows(), mock_source_pack(request))
    assert json.loads((tmp_path / "visualization_summary.json").read_text(encoding="utf-8"))["rows_total"] == 2
    assert "time_series_data" in paths

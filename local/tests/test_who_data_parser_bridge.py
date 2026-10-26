from __future__ import annotations

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.who_data_parser_bridge import (
    normalize_who_field_name,
    rows_from_csv_preview,
    rows_from_html_table,
    rows_from_json_payload,
)


def _query():
    return build_disease_time_query_request(
        disease="Ebola",
        country_or_region="Democratic Republic of the Congo",
        date_start="2025-01-01",
        date_end="2025-12-31",
    )


def test_field_mapping_supports_who_style_names() -> None:
    assert normalize_who_field_name("Health zone") == "region"
    assert normalize_who_field_name("Confirmed cases") == "confirmed_cases"
    assert normalize_who_field_name("Deaths") == "confirmed_deaths"
    assert normalize_who_field_name("Contacts under monitoring") == "contacts_under_monitoring"


def test_csv_preview_rows_become_pending_review_event_rows() -> None:
    rows, report = rows_from_csv_preview(
        "date,country,province,confirmed_cases,confirmed_deaths\n"
        "2025-02-03,Democratic Republic of the Congo,Equateur,7,2\n",
        query_request=_query(),
        source_id="WHO1",
    )

    assert len(rows) == 1
    assert rows[0]["confirmed_cases"] == 7
    assert rows[0]["approval_status"] == "pending_review"
    assert report.mapped_fields["province"] == "region"


def test_html_and_json_previews_are_supported() -> None:
    html_rows, _html_report = rows_from_html_table(
        "<table><tr><th>date</th><th>health zone</th><th>cases</th></tr>"
        "<tr><td>2025-03-03</td><td>Mbandaka</td><td>4</td></tr></table>",
        query_request=_query(),
        source_id="WHO2",
    )
    json_rows, _json_report = rows_from_json_payload(
        {"records": [{"date": "2025-04-07", "region": "North Kivu", "confirmed": 3}]},
        query_request=_query(),
        source_id="WHO3",
        records_path="records",
    )

    assert html_rows[0]["region"] == "Mbandaka"
    assert json_rows[0]["confirmed_cases"] == 3

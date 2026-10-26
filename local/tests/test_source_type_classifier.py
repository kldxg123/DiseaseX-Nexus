from __future__ import annotations

from live_source_search_orchestrator.source_type_classifier import classify_source_type


def test_source_type_classifier_covers_common_data_shapes():
    assert classify_source_type(title="Download CSV data", url="https://x.gov/a.csv", snippet="").source_type == "csv_dataset"
    assert classify_source_type(title="API JSON data", url="https://x.gov/api", snippet="json").source_type == "json_api"
    assert classify_source_type(title="Case table", url="https://x.gov/table", snippet="table").source_type == "html_table"
    assert classify_source_type(title="Interactive dashboard", url="https://x.gov/dash", snippet="dashboard").source_type == "dashboard_data_source"
    assert classify_source_type(title="Weekly report", url="https://x.gov/report", snippet="weekly").source_type == "official_report_page"
    assert classify_source_type(title="News item", url="https://healthnews.example/a", snippet="outbreak").source_type == "news_or_media"
    assert classify_source_type(title="Unrelated", url="https://example.org/a", snippet="weather").source_type == "low_relevance"

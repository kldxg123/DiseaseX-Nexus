from __future__ import annotations

from apps.dashboard import pages


def test_dashboard_page_functions_are_importable_without_streamlit_runtime() -> None:
    expected = [
        "render_overview_page",
        "render_sources_page",
        "render_ingestion_page",
        "render_events_page",
        "render_features_page",
        "render_models_page",
        "render_feedback_page",
        "render_minimum_data_page",
        "render_data_quality_page",
    ]

    for name in expected:
        assert callable(getattr(pages, name))

from __future__ import annotations

import httpx

from apps.dashboard.api_client import DashboardApiClient
from apps.dashboard.pages import render_data_quality_page
from tests.test_dashboard_business_pages import fake_streamlit_module


def test_dashboard_data_quality_api_client_methods_use_safe_endpoints() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    profile = client.get_data_quality_profile()
    audit = client.get_data_quality_label_audit(country="United States", disease="norovirus")
    report = client.build_data_quality_report({"write_files": False})

    assert profile["profile"]["source_registry"]["row_count"] == 1
    assert audit["label_audit"]["checked_features"] == 1
    assert report["summary"]["requires_human_review"] is True


def test_dashboard_data_quality_page_executes_with_fake_streamlit_without_service() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())
    with fake_streamlit_module() as fake:
        render_data_quality_page(client)

    rendered = str(fake.outputs).lower()
    assert "data quality" in rendered
    assert "human review" in rendered
    assert "feature_json" not in rendered
    assert "lesson_json" not in rendered


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data-quality/profile":
            return httpx.Response(
                200,
                json={
                    "profile": {
                        "source_registry": {"row_count": 1, "key_metrics": {"total": 1}, "warnings": []},
                        "raw_documents": {"row_count": 2, "key_metrics": {"duplicate_hash_count": 0}, "warnings": []},
                        "epidemic_events": {"row_count": 3, "key_metrics": {}, "warnings": []},
                        "model_features": {"row_count": 4, "key_metrics": {}, "warnings": []},
                        "predictions": {"row_count": 1, "key_metrics": {}, "warnings": []},
                        "human_feedback": {"row_count": 1, "key_metrics": {}, "warnings": []},
                        "model_experience_bank": {"row_count": 1, "key_metrics": {}, "warnings": []},
                        "overall_warnings": [],
                    },
                    "limitations": "human review required",
                },
            )
        if request.url.path == "/data-quality/label-audit":
            return httpx.Response(
                200,
                json={
                    "label_audit": {
                        "checked_features": 1,
                        "horizon_days_checked": [28],
                        "leakage_warnings": [],
                        "missing_target_warnings": [],
                        "inconsistent_rows": [],
                        "summary": {"requires_human_review": False},
                    },
                    "limitations": "rule based",
                },
            )
        if request.url.path == "/data-quality/report":
            return httpx.Response(
                200,
                json={
                    "summary": {"requires_human_review": True},
                    "warnings": [],
                    "limitations": ["human review required"],
                    "write_files": False,
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)

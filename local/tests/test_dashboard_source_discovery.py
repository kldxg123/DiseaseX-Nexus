from __future__ import annotations

import json
import sys
from contextlib import contextmanager

import httpx

from apps.dashboard import pages
from apps.dashboard.api_client import DashboardApiClient, DashboardApiError
from apps.dashboard.formatters import (
    compact_source_discovery_summary,
    rows_from_source_discovery_candidates,
    rows_from_source_discovery_connector_specs,
)


def _api_payload() -> dict:
    return {
        "status": "ok",
        "country": "United States",
        "disease": "norovirus",
        "search_results_seen": 2,
        "candidates_found": 2,
        "candidates_inserted_or_updated": 2,
        "skipped": 0,
        "connector_specs_generated": 1,
        "top_candidates": [
            {
                "name": "CDC NoroSTAT",
                "base_url": "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html",
                "domain": "cdc.gov",
                "source_type": "official",
                "source_score": 0.92,
                "requires_human_review": True,
                "status": "pending_review",
                "snippet": "do not show this snippet in dashboard table",
                "metadata_json": {"token": "hidden"},
            }
        ],
        "connector_specs": [
            {
                "connector_name": "cdc_norostat_connector",
                "source_name": "CDC NoroSTAT",
                "source_type": "official",
                "parser_strategy": "html_table",
                "sample_url_count": 1,
                "requires_human_review": True,
                "test_fixture_required": True,
                "sample_urls": ["https://www.cdc.gov/hidden"],
            }
        ],
        "warnings": ["Fixture-only discovery."],
        "network_accessed": False,
        "limitations": "human review required",
    }


def test_dashboard_api_client_source_discovery_uses_mock_transport_without_network() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_api_payload())

    client = DashboardApiClient("http://localhost:8000", transport=httpx.MockTransport(handler))
    payload = client.source_discovery_discover_fixture(
        country="United States",
        disease="norovirus",
        search_results={"results": []},
    )

    assert seen["path"] == "/source-discovery/discover-fixture"
    assert seen["body"]["country"] == "United States"
    assert payload["status"] == "ok"
    assert "metadata_json" not in str(payload)
    assert "do not show this snippet" not in str(payload)


def test_source_discovery_formatters_return_safe_rows() -> None:
    payload = _api_payload()

    summary = compact_source_discovery_summary(payload)
    candidates = rows_from_source_discovery_candidates(payload)
    connector_specs = rows_from_source_discovery_connector_specs(payload)
    rendered = json.dumps({"summary": summary, "candidates": candidates, "connector_specs": connector_specs})

    assert summary["candidates_found"] == 2
    assert candidates[0]["requires_human_review"] is True
    assert connector_specs[0]["sample_url_count"] == 1
    assert "snippet" not in rendered
    assert "metadata_json" not in rendered
    assert "sample_urls" not in rendered


def test_source_discovery_page_executes_with_fake_streamlit() -> None:
    client = _FakeDiscoveryClient()
    with fake_streamlit_module() as fake:
        pages.render_source_discovery_page(client)

    rendered = str(fake.outputs)
    assert client.called is True
    assert "数据源发现" in rendered
    assert "pending_review" in rendered
    assert "dry-run 安全失败" in rendered
    assert "人工审核" in rendered
    assert "本地 fixture/mock 样例" in rendered
    assert "metadata_json" not in rendered


class _FakeDiscoveryClient:
    def __init__(self) -> None:
        self.called = False
        self.live_called = False

    def source_discovery_discover_fixture(self, **kwargs) -> dict:
        self.called = True
        assert kwargs["country"] == "United States"
        assert isinstance(kwargs["search_results"], dict)
        return _api_payload()

    def source_discovery_live_controlled(self, payload: dict) -> dict:
        self.live_called = True
        assert payload["provider"] == "auto"
        assert payload["max_requests"] == 3
        return {
            **_api_payload(),
            "network_accessed": False,
            "request_count": 0,
            "candidate_urls_accessed": False,
            "follow_links": False,
            "status": "failed",
        }

    def live_source_search_dry_run(self, payload: dict) -> dict:
        assert payload["providers"] == "auto"
        raise DashboardApiError("API request failed: POST /live-source-search/dry-run status=500")

    def live_source_acquisition_plan(self, payload: dict) -> dict:
        assert payload["max_requests"] == 1
        return {"status": "blocked", "request_count": 0, "warnings": ["fixture plan"]}

    def live_source_acquisition_run(self, payload: dict) -> dict:
        assert payload["max_requests"] == 1
        return {"status": "failed", "request_count": 0, "warnings": ["fixture run"]}

    def live_source_acquisition_quality_diagnose(self, payload: dict) -> dict:
        assert payload["database_url"].startswith("sqlite:///")
        return {
            "status": "ok",
            "raw_documents_count": 0,
            "epidemic_events_count": 0,
            "model_features_count": 0,
            "recommended_next_action": "fixture",
        }


class FakeStreamlit:
    def __init__(self):
        self.outputs: list[object] = []
        self.session_state: dict[str, object] = {}

    def header(self, value):
        self.outputs.append(("header", value))

    def subheader(self, value):
        self.outputs.append(("subheader", value))

    def info(self, value):
        self.outputs.append(("info", value))

    def warning(self, value):
        self.outputs.append(("warning", value))

    def error(self, value):
        self.outputs.append(("error", value))

    def success(self, value):
        self.outputs.append(("success", value))

    def caption(self, value):
        self.outputs.append(("caption", value))

    def code(self, value, language=None):
        self.outputs.append(("code", value, language))

    def dataframe(self, value, use_container_width=True):
        self.outputs.append(("dataframe", value, use_container_width))

    def divider(self):
        self.outputs.append(("divider",))

    def expander(self, label, expanded=False):
        self.outputs.append(("expander", label, expanded))
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def text_input(self, label, value=""):
        return value

    def text_area(self, label, value="", height=None, key=None):
        if key:
            return self.session_state.get(key, value)
        return value

    def number_input(self, label, min_value=None, max_value=None, value=0):
        return value

    def checkbox(self, label, value=False):
        return value

    def selectbox(self, label, options, index=0):
        return options[index]

    def button(self, label):
        return True


@contextmanager
def fake_streamlit_module():
    fake = FakeStreamlit()
    previous = sys.modules.get("streamlit")
    sys.modules["streamlit"] = fake
    try:
        yield fake
    finally:
        if previous is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = previous

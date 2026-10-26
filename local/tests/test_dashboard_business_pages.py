from __future__ import annotations

import sys
from contextlib import contextmanager

from apps.dashboard import pages


class FakeStreamlit:
    def __init__(self):
        self.outputs: list[object] = []

    def __getattr__(self, name):
        if name == "sidebar":
            return self
        raise AttributeError(name)

    def header(self, value):
        self.outputs.append(("header", value))

    def subheader(self, value):
        self.outputs.append(("subheader", value))

    def title(self, value):
        self.outputs.append(("title", value))

    def info(self, value):
        self.outputs.append(("info", value))

    def error(self, value):
        self.outputs.append(("error", value))

    def success(self, value):
        self.outputs.append(("success", value))

    def warning(self, value):
        self.outputs.append(("warning", value))

    def caption(self, value):
        self.outputs.append(("caption", value))

    def json(self, value):
        self.outputs.append(("json", value))

    def code(self, value, language=None):
        self.outputs.append(("code", value, language))

    def dataframe(self, value, use_container_width=True):
        self.outputs.append(("dataframe", value, use_container_width))

    def divider(self):
        self.outputs.append(("divider",))

    def metric(self, label, value):
        self.outputs.append(("metric", label, value))

    def set_page_config(self, **kwargs):
        self.outputs.append(("page_config", kwargs))

    def text_input(self, label, value=""):
        return value

    def text_area(self, label, value="", height=None, key=None):
        return value

    def number_input(self, label, min_value=None, max_value=None, value=0):
        return value

    def checkbox(self, label, value=False):
        return value

    def selectbox(self, label, options):
        return options[0]

    def radio(self, label, options, index=0):
        return options[index]

    def button(self, label):
        return False

    def columns(self, count):
        return [_FakeContext(self) for _ in range(count)]

    def expander(self, label, expanded=False):
        return _FakeContext(self)


class _FakeContext:
    def __init__(self, st):
        self.st = st

    def __enter__(self):
        return self.st

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDashboardClient:
    def health(self):
        return {"status": "ok", "service": "api"}

    def get_source_health(self):
        return {"total": 1, "by_status": {"active": 1}, "recent_failed_sources": 0}

    def get_sources(self, **kwargs):
        return [{"id": 1, "name": "Fixture", "source_score": 0.9, "metadata_keys": ["source_score"]}]

    def seed_sources(self, **kwargs):
        return {"status": "ok", "summary": {"dry_run": True}}

    def review_source(self, source_id, payload):
        return {
            "status": "ok",
            "source": {"id": source_id, "status": payload["decision"]},
            "review": {"decision": payload["decision"], "notes_recorded": bool(payload.get("notes"))},
        }

    def crawl_mock_url(self, payload):
        return {"status": "ok", "summary": {"saved": True}}

    def parse_official_fixture(self, payload):
        return {"status": "ok", "summary": {"parsed": 1}}

    def discover_gdelt_mock(self, payload):
        return {"status": "ok", "summary": {"upserted": 0}}

    def source_discovery_discover_fixture(self, **kwargs):
        return {
            "status": "ok",
            "country": "United States",
            "disease": "norovirus",
            "search_results_seen": 1,
            "candidates_found": 1,
            "candidates_inserted_or_updated": 1,
            "connector_specs_generated": 0,
            "top_candidates": [
                {
                    "name": "Fixture",
                    "base_url": "https://fixture.invalid/source",
                    "domain": "fixture.invalid",
                    "source_type": "fixture",
                    "source_score": 0.7,
                    "requires_human_review": True,
                    "status": "pending_review",
                }
            ],
            "connector_specs": [],
            "warnings": [],
            "network_accessed": False,
        }

    def run_cleaner(self, payload):
        return {"status": "ok", "summary": {"processed": 0}}

    def get_events(self, **kwargs):
        return [{"event_date": "2026-01-01", "country": "A", "disease": "B", "cases": 3, "provenance_keys": []}]

    def build_features(self, payload):
        return {"status": "ok", "summary": {"feature_rows": 0}}

    def get_features(self, **kwargs):
        return [{"id": 1, "feature_keys": ["current_week_cases"], "target_value": 3}]

    def backtest_model(self, payload):
        return {"status": "ok", "summary": {"backtest_records": 0}}

    def get_backtest_metrics(self, **kwargs):
        return {"model_name": "moving_average", "backtest_records": 0}

    def predict_model(self, payload):
        return {"status": "ok", "summary": {"prediction_rows": 0}}

    def model_promotion_check(self, payload):
        return {"promote": False, "limitations": "review required"}

    def get_predictions(self, **kwargs):
        return [{"target_date": "2026-02-01", "predicted_value": 4.0, "risk_level": "medium"}]

    def add_feedback(self, payload):
        return {"status": "ok", "summary": {"feedback_id": 1}}

    def build_experience(self, payload):
        return {"status": "ok", "summary": {"experience_id": 1}}

    def get_feedback_summary(self, **kwargs):
        return {"feedback_count": 1, "by_status": {"failure": 1}}

    def get_feedback(self, **kwargs):
        return [{"id": 1, "feedback_status": "failure", "notes_present": True}]

    def get_experience_summary(self, **kwargs):
        return {"experience_count": 1}

    def get_experiences(self, **kwargs):
        return [{"id": 1, "failure_reason": "under_prediction", "lesson_summary": {"review_required": True}}]

    def minimum_data_analysis(self, payload):
        return {
            "minimum_feature_set": ["case_history"],
            "evaluation_preview": [{"feature_groups": ["case_history"], "passed": True}],
            "learning_curve": [],
            "ablation_results": [],
            "explanation": "Auxiliary analysis only.",
        }

    def get_data_quality_profile(self):
        return {
            "profile": {
                "source_registry": {"row_count": 1, "key_metrics": {"total": 1}, "warnings": []},
                "raw_documents": {"row_count": 1, "key_metrics": {"duplicate_hash_count": 0}, "warnings": []},
                "epidemic_events": {"row_count": 1, "key_metrics": {}, "warnings": []},
                "model_features": {"row_count": 1, "key_metrics": {}, "warnings": []},
                "predictions": {"row_count": 1, "key_metrics": {}, "warnings": []},
                "human_feedback": {"row_count": 1, "key_metrics": {}, "warnings": []},
                "model_experience_bank": {"row_count": 1, "key_metrics": {}, "warnings": []},
                "overall_warnings": [],
            },
            "limitations": "human review required",
        }

    def get_data_quality_label_audit(self, **kwargs):
        return {
            "label_audit": {
                "checked_features": 1,
                "horizon_days_checked": [28],
                "leakage_warnings": [],
                "missing_target_warnings": [],
                "inconsistent_rows": [],
                "summary": {"requires_human_review": False},
            },
            "limitations": "rule based",
        }

    def build_data_quality_report(self, payload):
        return {"summary": {"requires_human_review": True}, "warnings": [], "write_files": False}


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


def test_dashboard_business_pages_execute_with_fake_streamlit_without_network() -> None:
    client = FakeDashboardClient()
    renderers = [
        pages.render_overview_page,
        pages.render_sources_page,
        pages.render_source_discovery_page,
        pages.render_ingestion_page,
        pages.render_events_page,
        pages.render_features_page,
        pages.render_models_page,
        pages.render_feedback_page,
        pages.render_minimum_data_page,
        pages.render_data_quality_page,
    ]

    with fake_streamlit_module() as fake:
        for renderer in renderers:
            renderer(client)

    rendered = str(fake.outputs)
    assert "feature_json" not in rendered
    assert "lesson_json" not in rendered
    assert "<html>" not in rendered
    assert "不保证未来准确率" in rendered

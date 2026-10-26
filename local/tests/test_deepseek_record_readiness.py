from __future__ import annotations

from deepseek_web_search_agent.extraction_quality_audit import classify_record_readiness


def test_record_readiness_requires_date_and_numeric_value_for_feature_ready() -> None:
    feature_ready = classify_record_readiness(
        {
            "disease": "influenza",
            "country": "United States",
            "source_url": "https://health.example.gov/report",
            "source_domain": "health.example.gov",
            "evidence_summary": "Safe summary reports 12 cases during 2026-W10.",
            "iso_week": "2026-W10",
            "metric_name": "cases",
            "metric_value": 12,
            "requires_human_review": True,
        }
    )
    missing_date = classify_record_readiness(
        {
            "disease": "influenza",
            "country": "United States",
            "source_url": "https://health.example.gov/report",
            "source_domain": "health.example.gov",
            "evidence_summary": "Safe summary reports 12 cases.",
            "metric_name": "cases",
            "metric_value": 12,
        }
    )
    missing_value = classify_record_readiness(
        {
            "disease": "influenza",
            "country": "United States",
            "source_url": "https://health.example.gov/report",
            "source_domain": "health.example.gov",
            "evidence_summary": "Safe summary reports a weekly update during 2026-W10.",
            "iso_week": "2026-W10",
        }
    )

    assert "feature_ready" in feature_ready.readiness
    assert "feature_ready" not in missing_date.readiness
    assert "insufficient_temporal_signal" in missing_date.readiness
    assert "feature_ready" not in missing_value.readiness
    assert "insufficient_numeric_signal" in missing_value.readiness


def test_missing_source_cannot_be_event_ready() -> None:
    audit = classify_record_readiness(
        {
            "disease": "influenza",
            "country": "United States",
            "evidence_summary": "Safe summary reports 12 cases during 2026-W10.",
            "iso_week": "2026-W10",
            "metric_name": "cases",
            "metric_value": 12,
        }
    )

    assert "event_ready" not in audit.readiness
    assert "insufficient_source_grounding" in audit.readiness


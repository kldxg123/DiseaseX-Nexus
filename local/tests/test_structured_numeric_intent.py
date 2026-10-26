from __future__ import annotations

from deepseek_web_search_agent.structured_numeric_intent import build_structured_numeric_evidence_intent


def test_ebola_african_region_intent_targets_situation_reports_and_numeric_fields() -> None:
    intent = build_structured_numeric_evidence_intent(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )

    query_text = " ".join(intent.query_plan.query_terms).lower()

    assert intent.query_plan.query_intent == "structured_numeric_evidence"
    assert "situation report" in query_text
    assert "cases" in query_text
    assert "deaths" in query_text
    assert "contacts" in query_text
    assert "who afro" in query_text
    assert "africa cdc" in query_text
    assert "confirmed_cases" in intent.target_fields
    assert "contacts_under_monitoring" in intent.target_fields
    assert intent.query_plan.provider_targets[:3] == ["tavily", "who_afro", "africa_cdc"]

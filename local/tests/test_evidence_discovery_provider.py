from __future__ import annotations

from evidence_discovery_agent import MockEvidenceProvider, build_disease_task


def test_mock_provider_loads_fixtures_without_network() -> None:
    provider = MockEvidenceProvider()
    task = build_disease_task(
        disease="influenza",
        country="United States",
        prediction_target="outbreak_or_case_trend",
    )

    documents = provider.discover(task)

    assert provider.network_accessed is False
    assert documents
    assert any(document.disease == "influenza" for document in documents)
    assert any(document.disease == "generic" for document in documents)
    assert all(document.requires_human_review for document in documents)
    rendered = " ".join(document.summary for document in documents).lower()
    assert "<html" not in rendered
    assert "raw_text" not in rendered


def test_mock_provider_filters_norovirus_and_generic() -> None:
    provider = MockEvidenceProvider()
    task = build_disease_task(
        disease="norovirus",
        country="United States",
        prediction_target="outbreak_or_case_trend",
    )

    documents = provider.discover(task)

    diseases = {document.disease for document in documents}
    assert "norovirus" in diseases
    assert "generic" in diseases
    assert "influenza" not in diseases

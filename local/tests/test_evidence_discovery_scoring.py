from __future__ import annotations

from evidence_discovery_agent import MockEvidenceProvider, build_disease_task, score_evidence_records


def test_evidence_scoring_prefers_relevant_feasible_documents() -> None:
    task = build_disease_task(
        disease="influenza",
        country="United States",
        prediction_target="outbreak_or_case_trend",
    )
    documents = MockEvidenceProvider().discover(task)

    records = score_evidence_records(task, documents)

    assert records
    assert records[0].total_score >= records[-1].total_score
    assert all(0.0 <= record.total_score <= 1.0 for record in records)
    assert all(record.requires_human_review for record in records)
    assert any("MAE" in record.evaluation_metrics for record in records)

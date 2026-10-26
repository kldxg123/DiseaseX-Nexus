from __future__ import annotations

from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.candidate_quality_gate import apply_quality_gate, assess_candidate_quality
from live_source_search_orchestrator.candidate_scorer import score_candidates
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases


def _candidate(title: str, url: str, snippet: str = ""):
    aliases = expand_disease_aliases("measles")
    normalized = normalize_search_result(
        {"title": title, "url": url, "snippet": snippet, "source_name": title, "rank": 1},
        provider="official_directory",
        country="United States",
        aliases=aliases,
    )
    assert normalized is not None
    return score_candidates([normalized], aliases=aliases)[0]


def test_jobs_and_careers_are_rejected_or_strongly_deprioritized():
    candidate = _candidate(
        "CDC Careers measles program job",
        "https://jobs.cdc.gov/measles-careers",
        "Hiring and employment page for public health jobs.",
    )

    updated, assessment = assess_candidate_quality(candidate)

    assert updated.quality_gate_status == "rejected"
    assert updated.refined_score <= 0.18
    assert assessment.rejection_reason == "jobs_or_career_page"


def test_surveillance_dashboard_needs_metadata_check():
    candidate = _candidate(
        "Measles surveillance dashboard data",
        "https://www.cdc.gov/measles/data/dashboard",
        "United States measles cases dashboard table weekly surveillance data.",
    )

    updated, assessment = assess_candidate_quality(candidate)

    assert updated.quality_gate_status in {"needs_metadata_check", "pass"}
    assert "authority_domain" in assessment.quality_boosts
    assert updated.refined_score > 0.4


def test_apply_quality_gate_sorts_rejected_to_bottom():
    good = _candidate(
        "Measles weekly surveillance report",
        "https://www.cdc.gov/measles/data-research/index.html",
        "United States cases weekly report table surveillance data.",
    )
    bad = _candidate("CDC Jobs", "https://jobs.cdc.gov/careers", "employment hiring")

    gated, assessments = apply_quality_gate([bad, good])

    assert len(assessments) == 2
    assert gated[-1].quality_gate_status == "rejected"

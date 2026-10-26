from __future__ import annotations

from common.db import get_session
from crawler_optimizer_agent import CrawlerOptimizerAgent
from crawler_optimizer_agent.failure_analysis import classify_raw_document_failure
from tests.crawler_optimizer_test_utils import add_document, add_source, init_test_db


def test_optimizer_does_not_flag_structured_cdc_dataset_row_as_low_relevance(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(
            session,
            name="CDC NoroSTAT",
            metadata={"connector": "cdc_norostat"},
            score=0.9,
        )
        source.source_type = "official_dataset"
        document = add_document(
            session,
            source,
            title="CDC NoroSTAT structured row",
            body="Week label row with structured official dataset values only.",
            metadata={
                "source_key": "cdc_norostat",
                "source_type": "official_dataset",
                "country_detected": "United States",
                "disease_detected": "norovirus",
                "structured_event": {
                    "country": "United States",
                    "disease": "norovirus",
                    "week_label": "1-Aug",
                    "source_type": "official_dataset",
                },
                "row_fields": {"week": "1-Aug"},
            },
            url_suffix="cdc-norostat-row",
        )

        cases = classify_raw_document_failure(
            document,
            target_country="United States",
            target_disease="norovirus",
        )
        report = CrawlerOptimizerAgent(session).analyze(country="United States", disease="norovirus")

    assert {case.failure_type for case in cases}.isdisjoint({"low_relevance", "empty_content"})
    failure_types = [case["failure_type"] for case in report.failure_cases]
    assert "low_relevance" not in failure_types
    assert "empty_content" not in failure_types

from __future__ import annotations

import json

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.staging_review import write_structured_table_review


def test_zero_row_diagnostics_include_granular_blockers(tmp_path) -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    bundle = write_structured_table_review(
        output_dir=tmp_path,
        query_request=request,
        sources=[],
        extracted_rows=[],
        cleaned_rows=[],
        validation_issues=[],
        duplicate_groups=[],
        source_pack_readiness={
            "source_count": 1,
            "source_pack_level_blockers": [
                "numeric_signal_without_date",
                "api_hint_without_data",
                "document_candidate_not_downloaded",
                "needs_structured_document_or_dataset",
            ],
        },
    )

    payload = json.loads(open(bundle.report_paths["validation_issues"], encoding="utf-8").read())
    issue_types = {issue["issue_type"] for issue in payload["issues"]}

    assert "numeric_signal_without_date" in issue_types
    assert "api_hint_without_data" in issue_types
    assert "document_candidate_not_downloaded" in issue_types
    assert "needs_structured_document_or_dataset" in issue_types

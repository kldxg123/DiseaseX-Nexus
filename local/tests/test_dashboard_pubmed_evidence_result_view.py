from __future__ import annotations

import json

from apps.dashboard.formatters import sanitize_dashboard_payload


def test_pubmed_evidence_result_view_hides_full_abstract_and_raw_xml() -> None:
    visible = sanitize_dashboard_payload(
        {
            "status": "ok",
            "provider": "pubmed",
            "evidence_documents": [
                {
                    "title": "Influenza forecasting evidence",
                    "year": 2024,
                    "journal": "Example Journal",
                    "model_mentions": ["baseline"],
                    "full_abstract": "This full abstract should not be displayed.",
                    "raw_xml": "<Article>hidden</Article>",
                }
            ],
            "feature_candidates": [{"feature_group": "case_history", "score": 0.8}],
        }
    )

    rendered = json.dumps(visible, ensure_ascii=False).lower()
    assert visible["provider"] == "pubmed"
    assert visible["evidence_documents"][0]["title"] == "Influenza forecasting evidence"
    assert "full_abstract" not in rendered
    assert "raw_xml" not in rendered
    assert "<article" not in rendered

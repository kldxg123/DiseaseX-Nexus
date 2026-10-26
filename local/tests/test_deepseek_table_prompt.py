from __future__ import annotations

import json

from deepseek_web_search_agent.deepseek_table_prompt import (
    build_structured_event_table_prompt,
    prompt_hash,
    prompt_summary,
)
from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import mock_source_pack


def test_structured_table_prompt_is_strict_json_and_hashable() -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    sources = mock_source_pack(request)

    prompt = build_structured_event_table_prompt(request, sources)
    payload = json.loads(prompt)

    assert payload["task"].startswith("Extract candidate")
    assert payload["query"]["disease"] == "Ebola"
    assert "Return one valid JSON object only" in payload["rules"][-1]
    assert len(prompt_hash(prompt)) == 64


def test_prompt_summary_excludes_full_prompt_and_completion() -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    summary = prompt_summary(request, mock_source_pack(request))

    assert summary["task"] == "structured_event_table_extraction"
    assert "prompt" not in summary
    assert "completion" not in summary

from __future__ import annotations

import json
from pathlib import Path

from tests.test_live_source_discovery_postmortem import _synthetic_429_run
from scripts.analyze_live_source_discovery_run import analyze_live_source_discovery_run


def test_fallback_readiness_plan_generated_for_429(tmp_path) -> None:
    result = analyze_live_source_discovery_run(_synthetic_429_run(tmp_path))

    json_path = Path(result["fallback_readiness_plan_json"])
    markdown_path = Path(result["fallback_readiness_plan_markdown"])
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert json_path.exists()
    assert markdown_path.exists()
    assert payload["fallback_provider"] == "official_directory"
    assert payload["retry_gdelt_immediately"] is False
    assert payload["fallback_constraints"]["max_requests"] == 1
    assert payload["fallback_constraints"]["follow_links"] is False
    assert payload["fallback_constraints"]["candidate_urls_accessed"] is False
    assert payload["fallback_constraints"]["all_results_pending_review"] is True

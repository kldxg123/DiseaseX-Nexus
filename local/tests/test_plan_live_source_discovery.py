from __future__ import annotations

import json
from pathlib import Path

from scripts.plan_live_source_discovery import build_live_source_discovery_plan, write_live_source_discovery_plan


def test_plan_live_source_discovery_generates_json_and_markdown(tmp_path) -> None:
    result = write_live_source_discovery_plan(
        provider="gdelt",
        country="United States",
        disease="influenza",
        policy_path="configs/live_source_discovery_policy.example.json",
        output_dir=str(tmp_path / "plans"),
    )

    assert result["status"] == "ok"
    assert Path(result["plan_json"]).exists()
    assert Path(result["plan_markdown"]).exists()
    payload = json.loads(Path(result["plan_json"]).read_text(encoding="utf-8"))
    assert payload["provider"] == "gdelt"
    assert payload["network_accessed"] is False
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "password" not in rendered
    assert "token" not in rendered
    assert "secret" not in rendered


def test_build_live_source_discovery_plan_contains_required_sections() -> None:
    plan = build_live_source_discovery_plan(
        provider="gdelt",
        country="United States",
        disease="influenza",
        policy_path="configs/live_source_discovery_policy.example.json",
    )

    assert plan["preflight"]["status"] == "ok"
    assert plan["current_step_executes_live_discovery"] is False
    assert plan["policy_summary"]["free_web_search_allowed"] is False
    assert plan["acceptance_criteria"]
    assert plan["rollback_steps"]

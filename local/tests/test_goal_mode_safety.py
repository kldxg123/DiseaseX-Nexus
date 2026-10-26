from __future__ import annotations

import json

from deepseek_web_search_agent.auto_target_runner import run_goal_mode


def test_goal_mode_mock_summary_omits_raw_payloads_and_secrets(tmp_path) -> None:
    summary = run_goal_mode(
        targets="dengue:Global",
        date_start="2026-05-01",
        date_end="2026-07-01",
        output_dir=str(tmp_path / "goal"),
        mock_deepseek=True,
        mock_search=True,
    )

    text = json.dumps(summary.to_dict(), ensure_ascii=False).lower()

    assert summary.network_accessed is False
    for forbidden in ("api_key", "token", "password", "secret", "prompt", "completion", "response_body", "raw_text", "<html"):
        assert forbidden not in text

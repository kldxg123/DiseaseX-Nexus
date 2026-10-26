from __future__ import annotations

from src.data.filled_template_validator import validate_filled_templates


def test_filled_template_validator_reports_waiting_when_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = validate_filled_templates()
    assert result["filled_data_present"] is False
    assert result["v3_sft_train_allowed"] is False
    assert result["v3_preference_train_allowed"] is False
    assert result["blocked_reason"] == "waiting_for_manual_completed_data"

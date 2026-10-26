from __future__ import annotations

from src.eval.outbreak_risk_metrics import event_recall_and_lead_time, rare_event_metrics


def test_rare_event_metrics_prioritize_pr_auc_and_false_alarm_rate() -> None:
    result = rare_event_metrics([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1], threshold=0.5)
    assert "pr_auc" in result
    assert "recall_at_k" in result
    assert result["false_alarms_per_100_region_weeks"] == 25.0


def test_event_recall_and_lead_time() -> None:
    rows = [{"label": 1, "outbreak_id": "e1", "days_to_event": 14}, {"label": 1, "outbreak_id": "e2", "days_to_event": 7}]
    result = event_recall_and_lead_time(rows, [0.6, 0.2], threshold=0.5)
    assert result["event_recall"] == 0.5
    assert result["lead_time_weeks"] == 2.0

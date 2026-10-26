from src.reports.qwen_candidate_v2_eval_plan import build_eval_plan


def test_eval_plan_contains_promote_gate_and_no_auto_promote():
    plan = build_eval_plan()
    gate = plan["promote_gate"]

    assert plan["status"] == "ok"
    assert gate["json_validity_rate_min"] == 0.98
    assert gate["macro_f1_min"] == 0.70
    assert gate["up_recall_min"] == 0.50
    assert gate["up_precision_min"] == 0.35
    assert gate["false_alarm_rate_max"] == 0.15
    assert gate["evidence_validity_min"] == 0.95
    assert gate["promote_allowed_before_manual_confirmation"] is False
    assert plan["allow_auto_promote"] is False

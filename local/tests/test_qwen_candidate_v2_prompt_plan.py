from src.models.qwen25_prompt_templates import V2_CASE_TREND_RULES, prompt_plan_payload


def test_prompt_plan_contains_up_threshold_and_case_trend_evidence_rules():
    payload = prompt_plan_payload()
    rules = " ".join(V2_CASE_TREND_RULES)

    assert payload["status"] == "ok"
    assert "稳定不是默认安全答案" in rules
    assert "新增病例增长率" in rules
    assert "病例趋势依据" in payload["evidence_requirement"]

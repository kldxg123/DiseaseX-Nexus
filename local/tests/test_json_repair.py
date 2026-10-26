from src.utils.json_repair import repair_json_prediction


def test_repair_extracts_json_object():
    result = repair_json_prediction('prefix {"trend":"上升","confidence":0.8,"risk_score":0.7,"evidence":["a"]} suffix')
    assert result["trend"] == "上升"
    assert "error" not in result


def test_repair_fallback_has_error():
    result = repair_json_prediction("not json")
    assert result["trend"] == "稳定"
    assert result["error"]

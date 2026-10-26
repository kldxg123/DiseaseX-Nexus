from __future__ import annotations

from deepseek_web_search_agent.table_response_parser import parse_deepseek_table_response, parse_int_or_none


def test_parse_deepseek_table_response_from_fenced_json() -> None:
    response = """```json
    {"rows":[{"date":"2026-05-02","country":"USA","region":"A","confirmed_cases":"1,234","confirmed_deaths":"x","source_id":"SRC1","evidence_summary":"Grounded.","confidence":0.7}],"extraction_warnings":[]}
    ```"""

    parsed = parse_deepseek_table_response(response)

    assert parsed["rows"][0]["row_id"] == "ROW1"
    assert parsed["rows"][0]["confirmed_cases"] == 1234
    assert parsed["rows"][0]["confirmed_deaths"] is None
    assert "ROW1:confirmed_deaths:invalid_numeric" in parsed["extraction_warnings"]


def test_parse_int_or_none_is_strict() -> None:
    assert parse_int_or_none("42") == 42
    assert parse_int_or_none("+42") == 42
    assert parse_int_or_none("-1") == -1
    assert parse_int_or_none(True) is None
    assert parse_int_or_none("4.2") is None

from __future__ import annotations

from cleaner_agent.language import detect_language


def test_detect_language_for_english_and_chinese() -> None:
    assert detect_language("A norovirus outbreak was reported in a school.") == "en"
    assert detect_language("学校报告诺如病毒暴发。") == "zh"


def test_detect_language_empty_returns_none() -> None:
    assert detect_language("") is None
    assert detect_language(None) is None

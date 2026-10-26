from __future__ import annotations

from cleaner_agent.disease_detection import best_disease_match, detect_disease_mentions


def test_detect_norovirus_aliases_in_english_and_chinese() -> None:
    english = detect_disease_mentions("A norovirus outbreak was reported.")
    chinese = detect_disease_mentions("报告诺如病毒暴发。")

    assert english[0]["disease"] == "norovirus"
    assert chinese[0]["matched_term"] == "诺如病毒"


def test_acute_gastroenteritis_confidence_is_lower_than_norovirus() -> None:
    norovirus = best_disease_match("norovirus cases")
    acute = best_disease_match("acute gastroenteritis cases")

    assert norovirus is not None
    assert acute is not None
    assert acute["confidence"] < norovirus["confidence"]


def test_ebola_alias_and_irrelevant_text() -> None:
    assert best_disease_match("EVD alert")["disease"] == "ebola"
    assert detect_disease_mentions("library reading program") == []

from __future__ import annotations

from cleaner_agent.geo_detection import best_country_match, detect_country_mentions, detect_setting


def test_detect_country_mentions_for_us_aliases_and_chinese_names() -> None:
    assert best_country_match("Cases were reported in the USA.")["country"] == "United States"
    assert best_country_match("美国报告病例。")["country"] == "United States"
    assert detect_country_mentions("China and Japan reported updates.")


def test_detect_setting_keywords() -> None:
    assert detect_setting("outbreak at a school campus") == "school"
    assert detect_setting("cluster in a hospital clinic") == "hospital"
    assert detect_setting("cases in a nursing home") == "care_home"
    assert detect_setting("illness on a cruise ship") == "cruise"
    assert detect_setting("restaurant food service alert") == "restaurant"
    assert detect_setting("community transmission update") == "community"
    assert detect_setting("no relevant place") is None

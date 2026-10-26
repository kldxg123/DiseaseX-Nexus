from __future__ import annotations

from cleaner_agent.text_cleaning import clean_text, normalize_whitespace, remove_common_boilerplate


def test_clean_text_handles_none_and_whitespace() -> None:
    assert clean_text(None) == ""
    assert normalize_whitespace("a\n\n  b\tc") == "a b c"


def test_clean_text_removes_common_boilerplate_without_dropping_body() -> None:
    text = "Subscribe\nShare this article\nNorovirus report body. 广告 分享 正文继续。"

    cleaned = clean_text(text)

    assert "Subscribe" not in cleaned
    assert "Share this article" not in cleaned
    assert "广告" not in cleaned
    assert "Norovirus report body" in cleaned
    assert "正文继续" in cleaned


def test_remove_common_boilerplate_is_stable_for_empty_text() -> None:
    assert remove_common_boilerplate("") == ""

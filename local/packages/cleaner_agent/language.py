from __future__ import annotations

import re


def detect_language(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    if chinese_count == 0 and latin_count == 0:
        return None
    if chinese_count >= 2 and chinese_count >= latin_count * 0.2:
        return "zh"
    if latin_count > 0:
        return "en"
    return None

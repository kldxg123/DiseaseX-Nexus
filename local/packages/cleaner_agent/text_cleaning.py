from __future__ import annotations

import re


BOILERPLATE_PATTERNS = [
    "subscribe",
    "sign up",
    "cookie notice",
    "advertisement",
    "share this article",
    "订阅",
    "广告",
    "分享",
]


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def remove_common_boilerplate(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    for pattern in BOILERPLATE_PATTERNS:
        cleaned = re.sub(re.escape(pattern), " ", cleaned, flags=re.IGNORECASE)
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in BOILERPLATE_PATTERNS:
            continue
        lines.append(stripped)
    return "\n".join(lines) if lines else cleaned


def clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return normalize_whitespace(remove_common_boilerplate(str(text)))

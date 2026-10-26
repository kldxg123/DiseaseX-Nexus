from __future__ import annotations

import re
import unicodedata

_HYPHENS = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "﹘": "-", "﹣": "-", "－": "-"})


def normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).translate(_HYPHENS).strip()).casefold()

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata

from epidemic_core.ebola.fetcher import OfficialSourceFetcher


URL = "https://www.who.int/emergencies/disease-outbreak-news/item/2020-DON284"


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def text_from_html(raw: bytes) -> str:
    value = raw.decode("utf-8", errors="replace")
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value)
    value = re.sub(r"(?i)<(p|div|h[1-6]|li|br)[^>]*>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = unicodedata.normalize("NFKC", value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{2,}", "\n", value)
    return value.strip()


def main() -> None:
    fetcher = OfficialSourceFetcher({"www.who.int"}, max_bytes=8 * 1024 * 1024, use_environment_proxy=False)
    result = fetcher.fetch(URL)
    text = text_from_html(result.payload)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    hits = []
    for idx, line in enumerate(lines):
        low = line.lower()
        if ("3 470" in line or "3,470" in line or "3470" in line) or (
            "2287" in line or "2 287" in line or "2,287" in line
        ) or ("declared the end" in low and "ebola" in low):
            hits.append({"line": idx + 1, "text_sha256": sha_text(line), "prefix": line[:160]})
    normalized = " ".join(lines)
    patterns = {
        "total_cases": r"(?:total of|total)\s+([0-9][0-9,\s]{2,})\s+(?:EVD\s+)?cases",
        "confirmed_probable": r"([0-9][0-9,\s]{2,})\s+confirmed\s+and\s+([0-9][0-9,\s]{1,})\s+probable",
        "deaths": r"([0-9][0-9,\s]{2,})\s+deaths",
    }
    parsed = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, normalized, re.I)
        if m:
            parsed[key] = [int(re.sub(r"\D", "", g)) for g in m.groups()]
    print(
        json.dumps(
            {
                "url": URL,
                "final_url": result.final_url,
                "status": result.status,
                "mime": result.mime_type,
                "byte_size": len(result.payload),
                "sha256": result.sha256,
                "hits": hits[:8],
                "parsed": parsed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

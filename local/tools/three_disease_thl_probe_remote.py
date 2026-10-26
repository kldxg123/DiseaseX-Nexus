from __future__ import annotations

import json
import re
import urllib.request


URLS = [
    "https://sampo.thl.fi/pivot/prod/en/ttr/casesweek/fact_ttr_casesweek",
    "https://sampo.thl.fi/pivot/prod/en/ttr/casesweek/fact_ttr_casesweek.dimensions.json",
    "https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases",
    "https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases.dimensions.json",
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-thl-probe/1.0", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def main() -> None:
    out = {}
    for url in URLS:
        try:
            text = fetch(url)
            out[url] = {
                "len": len(text),
                "puumala_hits": [m.start() for m in re.finditer("Puumala", text, re.I)][:20],
                "nidr_near_puumala": [
                    text[max(0, m.start() - 300) : m.start() + 500] for m in re.finditer("Puumala", text, re.I)
                ][:5],
                "url_patterns": sorted(set(re.findall(r"fact_ttr_casesweek[^'\"\\s<>)]{0,120}|fact_ttr_cases[^'\"\\s<>)]{0,120}", text)))[:80],
                "export_hrefs": sorted(set(re.findall(r'href="([^"]*(?:fact_ttr_casesweek|fact_ttr_cases)[^"]*(?:csv|xlsx|xls|pdf)[^"]*)"', text)))[:80],
                "load_patterns": sorted(set(re.findall(r"thl\\.pivot\\.[A-Za-z0-9_]+|load[A-Za-z0-9_]+", text)))[:80],
                "prefix": text[:700],
            }
        except Exception as exc:
            out[url] = {"error": type(exc).__name__, "detail": str(exc)[:300]}
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

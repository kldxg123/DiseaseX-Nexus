from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path


URLS = {
    "cdc_legionella": "https://data.cdc.gov/resource/x9gk-5huc.json?"
    + urllib.parse.urlencode({"$limit": "5", "label": "Legionellosis"}),
    "cdc_hps": "https://data.cdc.gov/resource/x9gk-5huc.json?"
    + urllib.parse.urlencode({"$limit": "5", "label": "Hantavirus pulmonary syndrome"}),
    "cdc_meta": "https://data.cdc.gov/api/views/x9gk-5huc",
    "thl_week_dims": "https://sampo.thl.fi/pivot/prod/en/ttr/casesweek/fact_ttr_casesweek.dimensions.json",
    "thl_month_dims": "https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases.dimensions.json",
    "thl_license": "https://yhteistyotilat.fi/wiki08/spaces/THLKA/pages/43845120/THL+s+open+data+license+and+limitation+of+liability",
    "ecdc_datasets": "https://atlas.ecdc.europa.eu/public/AtlasService/rest/GetDatasets",
    "ecdc_atlas": "https://www.ecdc.europa.eu/en/surveillance-atlas-infectious-diseases",
    "who_don594": "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON594",
    "who_api": "https://www.who.int/api/news/diseaseoutbreaknews?$top=5",
}


def fetch(url: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 epidemic-openclaw-official-source-probe/1.0",
            "Accept": "application/json,text/html,text/csv,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
        payload = response.read(2048)
        return {
            "status": response.status,
            "content_type": response.headers.get("content-type"),
            "last_modified": response.headers.get("last-modified"),
            "etag": response.headers.get("etag"),
            "prefix": payload.decode("utf-8", "replace")[:900],
        }


def main() -> None:
    out = {}
    for key, url in URLS.items():
        try:
            out[key] = {"url": url, **fetch(url)}
        except Exception as exc:
            out[key] = {"url": url, "error": type(exc).__name__, "detail": str(exc)[:300]}
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

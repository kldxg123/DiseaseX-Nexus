from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request


BASE = (
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/"
    "sub_themes/respiratory/topics/Influenza/geography_types/Region/"
    "geographies/{geography}/metrics/influenza_testing_positivityByWeek"
)
REGIONS = (
    "North East",
    "North West",
    "Yorkshire and The Humber",
    "East Midlands",
    "West Midlands",
    "East of England",
    "London",
    "South East",
    "South West",
)


def probe(region: str) -> dict[str, object]:
    url = BASE.format(geography=urllib.parse.quote(region, safe="")) + "?format=json&page_size=1"
    request = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-source-preflight/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            body = response.read(256 * 1024)
            payload = json.loads(body)
            results = payload.get("results")
            return {
                "region": region,
                "http_status": response.status,
                "content_type": response.headers.get("Content-Type"),
                "count": payload.get("count"),
                "result_shape_valid": isinstance(results, list) and len(results) <= 1,
                "has_next": bool(payload.get("next")),
            }
    except urllib.error.HTTPError as exc:
        return {"region": region, "http_status": exc.code, "count": None, "result_shape_valid": False}
    except Exception as exc:
        return {"region": region, "error": type(exc).__name__, "count": None, "result_shape_valid": False}


print(json.dumps([probe(region) for region in REGIONS], sort_keys=True))

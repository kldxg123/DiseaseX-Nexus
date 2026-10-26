from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


URLS = [
    "https://api.ukhsa-dashboard.data.gov.uk/v2/themes/infectious_disease/sub_themes/respiratory/topics/Influenza/geography_types/Nation/geographies/England/metrics/influenza_testing_positivityByWeek?format=json&page_size=1",
    "https://api.ukhsa-dashboard.data.gov.uk/v2/themes/infectious_disease/sub_themes/respiratory/topics/influenza/geography_types/Nation/geographies/England/metrics/influenza_testing_positivityByWeek?format=json&page_size=1",
    "https://api.ukhsa-dashboard.data.gov.uk/health/",
]


for url in URLS:
    request = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-source-preflight/1.0"})
    result: dict[str, object] = {"url": url}
    try:
        with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            payload = json.loads(response.read(1024 * 1024))
            result.update(http_status=response.status, json_valid=True)
            if isinstance(payload, dict):
                result["keys"] = sorted(payload)[:20]
                result["count"] = payload.get("count")
                result["results_count"] = len(payload.get("results") or []) if "results" in payload else None
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
    except Exception as exc:
        result["error"] = type(exc).__name__
    print(json.dumps(result, sort_keys=True))

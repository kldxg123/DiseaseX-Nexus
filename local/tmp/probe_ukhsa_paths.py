from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


URLS = [
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics?format=json",
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics/?format=json",
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics/Influenza?format=json",
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics/influenza?format=json",
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics/Influenza/geography_types/?format=json",
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics/influenza/geography_types/?format=json",
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics/Influenza/geography_types/Nation/geographies/England/metrics/influenza_testing_positivityByWeek?format=json&page_size=1",
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/sub_themes/respiratory/topics/influenza/geography_types/Nation/geographies/England/metrics/influenza_testing_positivityByWeek?format=json&page_size=1",
]


for url in URLS:
    request = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-source-preflight/1.0"})
    item: dict[str, object] = {"url": url}
    try:
        with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            body = response.read(1024 * 1024)
            item.update(http_status=response.status, content_type=response.headers.get("Content-Type"))
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                item["json_valid"] = False
            else:
                item["json_valid"] = True
                if isinstance(payload, dict):
                    item["keys"] = sorted(payload)[:20]
                    item["count"] = payload.get("count")
                    if "results" in payload:
                        item["results_count"] = len(payload.get("results") or [])
                elif isinstance(payload, list):
                    item["list_count"] = len(payload)
    except urllib.error.HTTPError as exc:
        item["http_status"] = exc.code
    except Exception as exc:
        item["error"] = type(exc).__name__
    print(json.dumps(item, sort_keys=True))

from __future__ import annotations

import json
import ssl
import urllib.request


ROOT = (
    "https://api.ukhsa-dashboard.data.gov.uk/themes/infectious_disease/"
    "sub_themes/respiratory/topics/Influenza"
)


def get(path: str) -> object:
    request = urllib.request.Request(
        ROOT + path + ("&" if "?" in path else "?") + "format=json",
        headers={"User-Agent": "epidemic-openclaw-source-preflight/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
        return json.loads(response.read(1024 * 1024))


geotypes = get("/geography_types")
print(json.dumps({"geography_types": geotypes}, sort_keys=True))
for candidate in ("Nation", "Region", "UKHSA Region", "Upper Tier Local Authority"):
    try:
        geographies = get(f"/geography_types/{candidate}/geographies")
    except Exception as exc:
        print(json.dumps({"geography_type": candidate, "error": type(exc).__name__}, sort_keys=True))
        continue
    print(json.dumps({"geography_type": candidate, "geographies": geographies}, sort_keys=True))

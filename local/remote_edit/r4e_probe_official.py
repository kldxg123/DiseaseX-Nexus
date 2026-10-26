from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import requests

UA = "epidemic-openclaw-r4e-source-verification/1.0 (research data audit)"
URLS = [
    "https://www.cdc.gov/fluview/overview/index.html",
    "https://www.cdc.gov/fluview/overview/fluview-interactive.html",
    "https://gis.cdc.gov/grasp/fluview/fluportaldashboard.html",
    "https://gis.cdc.gov/grasp/fluview/FluViewPhase2QuickReferenceGuide.pdf",
]


def main() -> None:
    out = []
    for url in URLS:
        r = requests.get(url, timeout=30, headers={"User-Agent": UA})
        item = {
            "url": url,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "status_code": r.status_code,
            "final_url": r.url,
            "content_type": r.headers.get("content-type"),
            "content_length_header": r.headers.get("content-length"),
            "byte_length": len(r.content),
            "etag": r.headers.get("etag"),
            "last_modified": r.headers.get("last-modified"),
            "sha256": hashlib.sha256(r.content).hexdigest(),
            "resources": [],
            "sample": "",
        }
        ctype = r.headers.get("content-type", "")
        if "text" in ctype or "html" in ctype:
            text = r.text
            item["sample"] = re.sub(r"\s+", " ", text[:2000])
            resources = re.findall(r"""(?:src|href)=["']([^"']+)["']""", text)
            item["resources"] = [
                x for x in resources if any(k in x.lower() for k in ["flu", "app", "js", "api", "download"])
            ][:200]
        out.append(item)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

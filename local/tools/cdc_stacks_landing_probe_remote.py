from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
URLS = [
    "https://stacks.cdc.gov/view/cdc/37575",
    "https://stacks.cdc.gov/view/cdc/37469",
    "https://stacks.cdc.gov/view/cdc/37757",
]


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def robots() -> dict[str, Any]:
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url("https://stacks.cdc.gov/robots.txt")
    try:
        rp.read()
        return {url: rp.can_fetch("epidemic-openclaw-review-summary/1.0", url) for url in URLS}
    except Exception as exc:
        return {"error_type": type(exc).__name__}


def fetch(url: str) -> dict[str, Any]:
    variants = [
        url,
        url + "/",
        url + "?utm_source=epidemic-openclaw-review-summary",
    ]
    attempts = []
    for variant in variants:
        req = urllib.request.Request(variant, headers={"User-Agent": "epidemic-openclaw-review-summary/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = resp.read(200_000)
                attempts.append(
                    {
                        "url": variant,
                        "ok": True,
                        "status": getattr(resp, "status", None),
                        "final_url": resp.geturl(),
                        "content_type": resp.headers.get("content-type"),
                        "bytes": len(body),
                        "body_sha256": hashlib.sha256(body).hexdigest(),
                    }
                )
        except urllib.error.HTTPError as exc:
            body = exc.read(20_000)
            attempts.append(
                {
                    "url": variant,
                    "ok": False,
                    "error_type": "HTTPError",
                    "status": exc.code,
                    "reason": exc.reason,
                    "headers_sha256": sha_text(str(exc.headers)),
                    "body_bytes": len(body),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        except Exception as exc:
            attempts.append({"url": variant, "ok": False, "error_type": type(exc).__name__, "error_sha256": sha_text(str(exc))})
    return {"base_url": url, "attempts": attempts}


def main() -> None:
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "robots": robots(),
        "results": [fetch(url) for url in URLS],
        "database_writes": 0,
        "trusted_snapshot_promotion_human_decision_writes": 0,
    }
    write_json("cdc_stacks_landing_probe.json", report)
    print(json.dumps({"status": "CDC_STACKS_LANDING_PROBE_COMPLETE", "urls": len(URLS)}, sort_keys=True))


if __name__ == "__main__":
    main()

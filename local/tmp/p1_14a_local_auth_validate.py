from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_pair(path: Path) -> tuple[str, str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[0], lines[1]


def build_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    encoded = None if data is None else urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST" if data is not None else "GET")
    with opener.open(req, timeout=15) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, response.geturl(), body


def login(base: str, creds: tuple[str, str]) -> urllib.request.OpenerDirector:
    opener = build_opener()
    request(opener, f"{base}/login")
    request(opener, f"{base}/login", {"username": creds[0], "password": creds[1]})
    return opener


def json_get(opener: urllib.request.OpenerDirector, url: str) -> dict[str, object]:
    status, _final_url, body = request(opener, url)
    payload = json.loads(body)
    rows = payload.get("rows") or payload.get("data") or payload.get("items") or []
    return {
        "status": status,
        "row_count": len(rows) if isinstance(rows, list) else payload.get("row_count"),
        "candidate_preview_included": payload.get("candidate_preview_included"),
        "interpolated": payload.get("interpolated"),
        "zero_filled": payload.get("zero_filled"),
        "message": payload.get("message") or payload.get("note") or payload.get("status_message"),
        "keys": sorted(payload.keys())[:20],
    }


def main() -> None:
    dash = login("http://127.0.0.1:8120", read_pair(ROOT / "secrets" / "dashboard_login.txt"))
    endpoints = {
        "series_default": "http://127.0.0.1:8120/api/v1/p1-12/trusted/series?disease=ebola-disease&country_iso3=COD",
        "series_5y": "http://127.0.0.1:8120/api/v1/p1-12/trusted/series?disease=ebola-disease&country_iso3=COD&time_preset=5y",
        "series_10y": "http://127.0.0.1:8120/api/v1/p1-12/trusted/series?disease=ebola-disease&country_iso3=COD&time_preset=10y",
        "major_current": "http://127.0.0.1:8120/api/v1/p1-12/trusted/series?disease=ebola-disease&country_iso3=COD&time_preset=major_outbreak&outbreak_id=bvd-drc-uganda-2026",
        "major_historical": "http://127.0.0.1:8120/api/v1/p1-12/trusted/series?disease=ebola-disease&country_iso3=COD&time_preset=major_outbreak&outbreak_id=ebola-drc-north-kivu-ituri-2018-2020",
        "ebola_options": "http://127.0.0.1:8120/api/v1/ebola/options",
    }
    checks: dict[str, object] = {"dashboard_api": {name: json_get(dash, url) for name, url in endpoints.items()}}
    try:
        review = login("http://127.0.0.1:8121", read_pair(ROOT / "secrets" / "source_review_login.txt"))
    except PermissionError:
        review = None
        checks["review_auth"] = {"credential_readable": False, "semantic_check": "SKIPPED"}
    if review is not None:
        status, _url, list_html = request(review, "http://127.0.0.1:8121/reviews")
        links = re.findall(r'href="(/reviews/[^"#?]+)', list_html)
        checks["review_list"] = {
            "status": status,
            "detail_links": len(links),
            "has_review_ready": "REVIEW_READY" in list_html,
            "has_sinan_text": "SINAN" in list_html or "sinan" in list_html,
        }
        if links:
            d_status, _d_url, detail_html = request(review, f"http://127.0.0.1:8121{links[0]}")
            checks["review_detail_first"] = {
                "status": d_status,
                "source_summary_card": 'id="source-summary-card"' in detail_html,
                "pending_not_trusted_marker": 'data-pending-not-trusted="true"' in detail_html,
                "technical_evidence_collapsed": "<details" in detail_html and "折叠技术证据" in detail_html,
                "approval_button_present": 'value="APPROVED"' in detail_html,
                "approval_disabled": 'value="APPROVED" disabled' in detail_html,
                "plain_language": all(term in detail_html for term in ("这个来源提供什么", "为什么进入人工复核", "尚未进入可信正式数据")),
            }
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

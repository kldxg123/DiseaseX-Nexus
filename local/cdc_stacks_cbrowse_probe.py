from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)
OUT = REPORT_DIR / "CDC_STACKS_CBROWSE_PROBE.json"
RAW = REPORT_DIR / "artifacts" / "cdc_stacks_cbrowse"
RAW.mkdir(parents=True, exist_ok=True)

URLS = [
    "https://stacks.cdc.gov/cbrowse?parentId=cdc%3A49375&terms=legionellosis&sortBy=pub_date_key+asc&start=0",
    "https://stacks.cdc.gov/cbrowse?parentId=cdc%3A49375&terms=legionellosis&sortBy=pub_date_key+asc&ss_publishyear%5B0%5D=2026&start=0",
]


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-cbrowse/1.0"})
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read(3_000_000)
            status = resp.status
            final_url = resp.geturl()
            headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read(1_000_000)
        status = exc.code
        final_url = exc.geturl()
        headers = dict(exc.headers.items())
    except Exception as exc:
        return {
            "request_url": url,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
            "fetched_at_utc": fetched_at,
        }
    digest = hashlib.sha256(body).hexdigest()
    name = "cbrowse_" + hashlib.sha256(url.encode()).hexdigest()[:12] + f"_{status}.html"
    raw_path = RAW / name
    raw_path.write_bytes(body)
    text = body.decode("utf-8", "replace")
    links = sorted(set(html.unescape(m) for m in re.findall(r'href=["\']([^"\']+)["\']', text)))
    cdc_links = []
    for link in links:
        abs_url = urllib.parse.urljoin(final_url, link)
        if re.match(r"https://stacks\.cdc\.gov/view/cdc/\d+$", abs_url):
            cdc_links.append(abs_url)
    next_links = []
    for link in links:
        abs_url = urllib.parse.urljoin(final_url, link)
        if "cbrowse" in abs_url and ("start=" in abs_url) and ("Next" in text[text.find(link)-300:text.find(link)+300] if link in text else True):
            next_links.append(abs_url)
    total_candidates = []
    for pat in [
        r"([0-9,]+)\s+results",
        r"Results\s+[0-9,]+\s*-\s*[0-9,]+\s+of\s+([0-9,]+)",
        r"Showing\s+[0-9,]+\s*(?:to|-)\s*[0-9,]+\s+of\s+([0-9,]+)",
    ]:
        total_candidates.extend(re.findall(pat, text, flags=re.I))
    start_values = sorted(set(int(x) for x in re.findall(r"[?&]start=([0-9]+)", text)))
    return {
        "request_url": url,
        "status": status,
        "final_url": final_url,
        "fetched_at_utc": fetched_at,
        "headers": {k: v for k, v in headers.items() if k.lower() in {"content-type", "etag", "last-modified", "cache-control", "date", "server"}},
        "body_sha256": digest,
        "body_size_bytes": len(body),
        "raw_file": str(raw_path.relative_to(REPORT_DIR)),
        "body_sample_redacted": text[:2000],
        "canonical_cdc_item_urls": cdc_links[:100],
        "canonical_cdc_item_count_on_page": len(cdc_links),
        "next_page_url_candidates": next_links[:20],
        "start_values_seen": start_values[:50],
        "pagination_step_candidates": sorted(set(b - a for a, b in zip(start_values, start_values[1:]) if b > a))[:10],
        "total_result_text_candidates": total_candidates[:10],
        "contains_legionellosis": "Legionellosis" in text or "legionellosis" in text.lower(),
        "contains_nndss_series": "National Notifiable Diseases: Infectious Weekly Tables" in text,
    }


def main() -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "READ_ONLY_CBROWSE_PROBE",
        "official_source": "CDC Stacks cbrowse",
        "requests": [fetch(url) for url in URLS],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "statuses": [r.get("status") for r in payload["requests"]], "item_counts": [r.get("canonical_cdc_item_count_on_page") for r in payload["requests"]]}, sort_keys=True))


if __name__ == "__main__":
    main()

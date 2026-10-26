from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)
ART = REPORT_DIR / "artifacts" / "cdc_stacks_legionellosis_enumeration"
DETAIL_DIR = ART / "details"
TXT_DIR = ART / "txt"
PAGE_DIR = ART / "pages"
for d in [ART, DETAIL_DIR, TXT_DIR, PAGE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

EXPORT_BASE = "https://stacks.cdc.gov/fedora/export/download/collection/cdc%3A49375"
ROWS = 500
MAX_PAGES = 80
MAX_RECORDS = 40000
MAX_BYTES = 90_000_000
TARGET_MIN_RECORDS = 120


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, max_bytes: int = 8_000_000) -> dict[str, Any]:
    fetched_at = utc_now()
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-cdc-stacks-enumerator/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(max_bytes + 1)
            return {
                "url": url,
                "status": resp.status,
                "final_url": resp.geturl(),
                "headers": {k: v for k, v in resp.headers.items() if k.lower() in {"content-type", "etag", "last-modified", "date", "server"}},
                "fetched_at_utc": fetched_at,
                "body": data,
                "truncated": len(data) > max_bytes,
            }
    except urllib.error.HTTPError as exc:
        data = exc.read(1_000_000)
        return {
            "url": url,
            "status": exc.code,
            "final_url": exc.geturl(),
            "headers": {k: v for k, v in exc.headers.items() if k.lower() in {"content-type", "etag", "last-modified", "date", "server"}},
            "fetched_at_utc": fetched_at,
            "body": data,
            "truncated": False,
            "error_type": "HTTPError",
        }
    except Exception as exc:
        return {"url": url, "status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)[:300], "fetched_at_utc": fetched_at, "body": b"", "truncated": False}


def title_of(doc: dict[str, Any]) -> str:
    value = doc.get("mods.title") or doc.get("dc.title") or ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def list_value(doc: dict[str, Any], key: str) -> list[str]:
    value = doc.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def parse_week(doc: dict[str, Any], title: str) -> tuple[int | None, int | None, str | None]:
    year = None
    week = None
    week_ending_text = None
    for key in ["mods.volume", "mods.ss_publishyear"]:
        if doc.get(key):
            try:
                year = int(str(doc.get(key)))
                break
            except Exception:
                pass
    if doc.get("mods.issue"):
        try:
            week = int(str(doc.get("mods.issue")))
        except Exception:
            pass
    m = re.search(r"\(Week\s+(\d{1,2})\).*?week ending ([A-Za-z]+\s+\d{1,2},\s+\d{4})", title)
    if m:
        week = int(m.group(1))
        week_ending_text = m.group(2)
        if year is None:
            year = int(week_ending_text[-4:])
    return year, week, week_ending_text


def is_legionellosis_doc(doc: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    title = title_of(doc)
    series = list_value(doc, "mods.related_series")
    pid = str(doc.get("PID") or "")
    ids = list_value(doc, "dc.identifier")
    canonical = next((x for x in ids if re.match(r"https://stacks\.cdc\.gov/view/cdc/\d+$", x)), None)
    year, week, week_ending_text = parse_week(doc, title)
    reasons = []
    if not title.startswith("Legionellosis:"):
        reasons.append("TITLE_NOT_LEGIONELLOSIS")
    if "National Notifiable Diseases: Infectious Weekly Tables" not in series:
        reasons.append("SERIES_NOT_NNDSS_INFECTIOUS_WEEKLY_TABLES")
    if not pid.startswith("cdc:"):
        reasons.append("PID_INVALID")
    if not canonical:
        reasons.append("CANONICAL_URL_MISSING")
    if year is None or week is None or not (1 <= week <= 53):
        reasons.append("YEAR_WEEK_INVALID")
    ok = not reasons
    return ok, {
        "pid": pid,
        "canonical_url": canonical,
        "title": title,
        "series": series,
        "mmwr_year": year,
        "mmwr_week": week,
        "week_ending_text": week_ending_text,
        "publish_year": doc.get("mods.ss_publishyear"),
        "createdDate": doc.get("fgs.createdDate"),
        "lastModifiedDate": doc.get("fgs.lastModifiedDate"),
        "ds2_sourceurl_from_export": (list_value(doc, "DS2.sourceurl") or [None])[0],
        "ds2_label_from_export": doc.get("DS2.label_txt_en"),
        "filter_ok": ok,
        "filter_reasons": reasons,
    }


def parse_ds2_from_detail(detail_text: str, canonical_url: str, export_url: str | None) -> list[str]:
    links = set()
    for raw in re.findall(r'href=["\']([^"\']+\.txt(?:\?[^"\']*)?)["\']', detail_text, flags=re.I):
        links.add(html.unescape(urllib.parse.urljoin(canonical_url, raw)))
    for raw in re.findall(r'https?://[^"\'>\s]+\.txt', detail_text, flags=re.I):
        links.add(html.unescape(raw))
    # Accept export URL only if the detail page visibly contains the exact URL or label.
    if export_url and (export_url in detail_text or urllib.parse.unquote(export_url) in detail_text):
        links.add(export_url)
    return sorted(links)


def download_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    canonical = candidate["canonical_url"]
    detail = fetch(canonical, max_bytes=2_000_000)
    detail_body = detail.pop("body")
    detail_sha = sha(detail_body)
    cid = candidate["pid"].replace(":", "_")
    detail_path = DETAIL_DIR / f"{cid}.html"
    detail_path.write_bytes(detail_body)
    detail_text = detail_body.decode("utf-8", "replace")
    ds2_urls = parse_ds2_from_detail(detail_text, canonical, candidate.get("ds2_sourceurl_from_export"))
    txt_result = None
    for url in ds2_urls:
        if not url.lower().endswith(".txt"):
            continue
        txt = fetch(url, max_bytes=500_000)
        txt_body = txt.pop("body")
        txt_sha = sha(txt_body)
        text = txt_body.decode("utf-8", "replace")
        if "Legionellosis" not in text or "Weekly cases" not in text:
            continue
        txt_path = TXT_DIR / f"{cid}_{txt_sha[:12]}.txt"
        txt_path.write_bytes(txt_body)
        txt_result = {
            **txt,
            "body_sha256": txt_sha,
            "body_size_bytes": len(txt_body),
            "raw_file": str(txt_path.relative_to(REPORT_DIR)),
            "contains_legionellosis": "Legionellosis" in text,
            "contains_required_national_row": "U.S. Residents, excluding U.S. Territories" in text,
        }
        break
    return {
        **candidate,
        "detail": {
            **detail,
            "body_sha256": detail_sha,
            "body_size_bytes": len(detail_body),
            "raw_file": str(detail_path.relative_to(REPORT_DIR)),
            "ds2_url_candidates_from_detail": ds2_urls,
        },
        "txt": txt_result,
        "detail_validation_ok": bool(ds2_urls),
        "txt_validation_ok": bool(txt_result and txt_result.get("contains_required_national_row")),
    }


def main() -> None:
    pages = []
    candidates: list[dict[str, Any]] = []
    total_bytes = 0
    num_found = None
    for page_no in range(MAX_PAGES):
        start = page_no * ROWS
        if start >= MAX_RECORDS:
            break
        url = f"{EXPORT_BASE}?rows={ROWS}&start={start}"
        resp = fetch(url, max_bytes=8_000_000)
        body = resp.pop("body")
        body_sha = sha(body)
        total_bytes += len(body)
        page_path = PAGE_DIR / f"collection_rows_{ROWS}_start_{start}_{body_sha[:12]}.json"
        page_path.write_bytes(body)
        page_info = {
            **resp,
            "start": start,
            "rows": ROWS,
            "body_sha256": body_sha,
            "body_size_bytes": len(body),
            "raw_file": str(page_path.relative_to(REPORT_DIR)),
            "candidate_count_on_page": 0,
        }
        if resp.get("status") != 200:
            pages.append(page_info)
            break
        data = json.loads(body.decode("utf-8", "replace"))
        response = data.get("response", {})
        docs = response.get("docs", [])
        num_found = response.get("numFound", num_found)
        for doc in docs:
            ok, item = is_legionellosis_doc(doc)
            if ok:
                candidates.append(item)
        page_info["doc_count"] = len(docs)
        page_info["numFound"] = num_found
        page_info["candidate_count_on_page"] = len([1 for doc in docs if is_legionellosis_doc(doc)[0]])
        pages.append(page_info)
        if len(candidates) >= TARGET_MIN_RECORDS or total_bytes >= MAX_BYTES:
            break
        time.sleep(0.8)

    # Prefer newest first for detail/TXT download; keep at least 104 available if present.
    candidates = sorted(candidates, key=lambda x: ((x["mmwr_year"] or 0), (x["mmwr_week"] or 0)), reverse=True)
    selected = candidates[:max(104, min(len(candidates), TARGET_MIN_RECORDS))]
    downloaded = []
    for cand in selected:
        downloaded.append(download_candidate(cand))
        time.sleep(0.8)
    keys: dict[str, list[str]] = {}
    for item in downloaded:
        key = f"{item.get('mmwr_year')}-W{int(item.get('mmwr_week') or 0):02d}"
        keys.setdefault(key, []).append(item["pid"])
    duplicate_keys = {k: v for k, v in keys.items() if len(v) > 1}
    sorted_keys = sorted(keys)
    matrix = {
        "observed_week_count": len(keys),
        "duplicate_week_keys": duplicate_keys,
        "first_week_key": sorted_keys[0] if sorted_keys else None,
        "last_week_key": sorted_keys[-1] if sorted_keys else None,
        "txt_valid_count": sum(1 for x in downloaded if x.get("txt_validation_ok")),
        "detail_valid_count": sum(1 for x in downloaded if x.get("detail_validation_ok")),
        "note": "Completeness is based on official collection export metadata; missing weeks are not converted to zero.",
    }
    manifest = {
        "generated_at_utc": utc_now(),
        "metadata_enumeration_mechanism": "CDC Stacks official collection export JSON",
        "limits": {
            "rows_per_page": ROWS,
            "max_pages": MAX_PAGES,
            "max_records": MAX_RECORDS,
            "max_download_bytes": MAX_BYTES,
            "request_interval_seconds": 0.8,
        },
        "pages": pages,
        "num_found": num_found,
        "candidate_count_total": len(candidates),
        "selected_count": len(selected),
        "downloaded_count": len(downloaded),
        "items": downloaded,
        "week_matrix": matrix,
        "gate_status": {
            "source_authenticity": "PASS" if downloaded else "FAIL_NO_ITEMS",
            "title_series_filter": "PASS" if all(x.get("filter_ok") for x in downloaded) else "FAIL",
            "canonical_urls_from_official_export": "PASS" if all(x.get("canonical_url", "").startswith("https://stacks.cdc.gov/view/cdc/") for x in downloaded) else "FAIL",
            "detail_page_ds2_parsed": "PASS" if all(x.get("detail_validation_ok") for x in downloaded) else "FAIL",
            "txt_downloaded_and_valid": "PASS" if all(x.get("txt_validation_ok") for x in downloaded) else "FAIL",
            "minimum_104_records": "PASS" if len(downloaded) >= 104 else "FAIL",
            "duplicate_weeks": "PASS" if not duplicate_keys else "FAIL",
        },
    }
    out = REPORT_DIR / "US_LEGIONELLOSIS_STACKS_ENUMERATION_MANIFEST.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "candidate_count_total": len(candidates),
        "downloaded_count": len(downloaded),
        "txt_valid_count": matrix["txt_valid_count"],
        "gate_status": manifest["gate_status"],
        "out": str(out),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

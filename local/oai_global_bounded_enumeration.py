from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)
ART = REPORT_DIR / "artifacts" / "cdc_stacks_oai_global_enumeration"
PAGE_DIR = ART / "pages"
DETAIL_DIR = ART / "details"
TXT_DIR = ART / "txt"
for d in [ART, PAGE_DIR, DETAIL_DIR, TXT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

OUT = REPORT_DIR / "CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION.json"
CHECKPOINT = REPORT_DIR / "CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION_CHECKPOINT.json"

CUTOFF = datetime.now(timezone.utc).replace(microsecond=0)
START = "2024-01-01T00:00:00Z"
UNTIL = CUTOFF.isoformat().replace("+00:00", "Z")
FIRST_URL = "https://stacks.cdc.gov/fedora/oai?" + urllib.parse.urlencode(
    {"verb": "ListRecords", "metadataPrefix": "oai_dc", "from": START, "until": UNTIL}
)
MAX_PAGES = 250
MAX_RECORDS = 30000
MAX_BYTES = 150_000_000
MAX_SECONDS = 25 * 60
REQUEST_INTERVAL_SECONDS = 3.0

OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def headers_dict(headers: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(headers.items()).items()}


def fetch(url: str, max_bytes: int = 10_000_000) -> dict[str, Any]:
    fetched_at = utc_now()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "epidemic-openclaw-oai-enumerator/1.0",
            "Accept": "application/xml,text/xml,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=75) as resp:
            body = resp.read(max_bytes + 1)
            return {
                "url": url,
                "status": resp.status,
                "final_url": resp.geturl(),
                "headers": headers_dict(resp.headers),
                "fetched_at_utc": fetched_at,
                "body": body,
                "truncated": len(body) > max_bytes,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(2_000_000)
        return {
            "url": url,
            "status": exc.code,
            "final_url": exc.geturl(),
            "headers": headers_dict(exc.headers),
            "fetched_at_utc": fetched_at,
            "body": body,
            "truncated": False,
            "error_type": "HTTPError",
        }
    except Exception as exc:
        return {
            "url": url,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
            "fetched_at_utc": fetched_at,
            "body": b"",
            "truncated": False,
        }


def parse_oai_page(body: bytes) -> dict[str, Any]:
    root = ET.fromstring(body)
    errors = [
        {"code": e.attrib.get("code"), "text": "".join(e.itertext()).strip()}
        for e in root.findall("oai:error", OAI_NS)
    ]
    records = []
    for rec in root.findall(".//oai:record", OAI_NS):
        hdr = rec.find("oai:header", OAI_NS)
        meta = rec.find("oai:metadata", OAI_NS)
        fields: dict[str, list[str]] = {}
        if meta is not None:
            for child in meta.findall(".//{http://purl.org/dc/elements/1.1/}*"):
                tag = child.tag.split("}", 1)[-1]
                fields.setdefault(tag, []).append((child.text or "").strip())
        identifier = (hdr.findtext("oai:identifier", default="", namespaces=OAI_NS) if hdr is not None else "").strip()
        datestamp = (hdr.findtext("oai:datestamp", default="", namespaces=OAI_NS) if hdr is not None else "").strip()
        records.append(
            {
                "identifier": identifier,
                "datestamp": datestamp,
                "setSpecs": [(x.text or "").strip() for x in hdr.findall("oai:setSpec", OAI_NS)] if hdr is not None else [],
                "dc_title": fields.get("title", []),
                "dc_identifier": fields.get("identifier", []),
                "dc_source": fields.get("source", []),
                "dc_relation": fields.get("relation", []),
                "dc_subject": fields.get("subject", []),
                "dc_description": fields.get("description", []),
            }
        )
    token = root.find(".//oai:resumptionToken", OAI_NS)
    token_text = (token.text or "").strip() if token is not None else ""
    return {
        "valid_oai": root.tag.endswith("OAI-PMH"),
        "errors": errors,
        "records": records,
        "resumption_token": token_text or None,
        "resumption_token_attributes": dict(token.attrib) if token is not None else {},
    }


def is_legionellosis_meta(record: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    title = " ".join(record.get("dc_title") or [])
    identifiers = record.get("dc_identifier") or []
    canonical = next((x for x in identifiers if re.match(r"https://stacks\.cdc\.gov/view/cdc/\d+$", x)), None)
    m = re.search(r"Legionellosis:\s*\(Week\s+(\d{1,2})\).*?week ending ([A-Za-z]+\s+\d{1,2},\s+\d{4})", title)
    reasons = []
    if "Legionellosis" not in title:
        reasons.append("TITLE_NOT_LEGIONELLOSIS")
    if not m:
        reasons.append("WEEK_TITLE_PATTERN_MISSING")
    if not canonical:
        reasons.append("CANONICAL_IDENTIFIER_MISSING")
    ok = not reasons
    year = int(m.group(2)[-4:]) if m else None
    week = int(m.group(1)) if m else None
    return ok, {
        "oai_identifier": record.get("identifier"),
        "datestamp": record.get("datestamp"),
        "title": title,
        "canonical_url": canonical,
        "dc_identifier": identifiers,
        "dc_source": record.get("dc_source") or [],
        "dc_relation": record.get("dc_relation") or [],
        "mmwr_year_from_title": year,
        "mmwr_week_from_title": week,
        "week_ending_text": m.group(2) if m else None,
        "metadata_filter_ok": ok,
        "metadata_filter_reasons": reasons,
    }


def parse_detail_for_ds2(detail: str, canonical: str) -> list[str]:
    links = set()
    for raw in re.findall(r'href=["\']([^"\']+\.txt(?:\?[^"\']*)?)["\']', detail, flags=re.I):
        links.add(html.unescape(urllib.parse.urljoin(canonical, raw)))
    for raw in re.findall(r'https?://[^"\'>\s]+\.txt', detail, flags=re.I):
        links.add(html.unescape(raw))
    return sorted(links)


def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    canonical = candidate["canonical_url"]
    detail = fetch(canonical, max_bytes=2_000_000)
    detail_body = detail.pop("body")
    detail_sha = sha(detail_body)
    cid = canonical.rsplit("/", 1)[-1]
    detail_path = DETAIL_DIR / f"cdc_{cid}.html"
    detail_path.write_bytes(detail_body)
    text = detail_body.decode("utf-8", "replace")
    series_ok = "National Notifiable Diseases: Infectious Weekly Tables" in text
    nndss_ok = "National Notifiable Diseases Surveillance System" in text or "NNDSS" in text
    ds2_urls = parse_detail_for_ds2(text, canonical)
    txt_info = None
    for url in ds2_urls:
        txt = fetch(url, max_bytes=500_000)
        txt_body = txt.pop("body")
        txt_sha = sha(txt_body)
        txt_text = txt_body.decode("utf-8", "replace")
        if "Legionellosis" not in txt_text or "Weekly cases" not in txt_text:
            continue
        txt_path = TXT_DIR / f"cdc_{cid}_{txt_sha[:12]}.txt"
        txt_path.write_bytes(txt_body)
        txt_info = {
            **txt,
            "body_sha256": txt_sha,
            "body_size_bytes": len(txt_body),
            "raw_file": str(txt_path.relative_to(REPORT_DIR)),
            "contains_required_national_row": "U.S. Residents, excluding U.S. Territories" in txt_text,
            "contains_legionellosis": "Legionellosis" in txt_text,
        }
        break
    return {
        **candidate,
        "detail": {
            **detail,
            "body_sha256": detail_sha,
            "body_size_bytes": len(detail_body),
            "raw_file": str(detail_path.relative_to(REPORT_DIR)),
            "series_ok": series_ok,
            "nndss_ok": nndss_ok,
            "ds2_url_candidates": ds2_urls,
        },
        "txt": txt_info,
        "candidate_validation_ok": bool(series_ok and nndss_ok and txt_info and txt_info.get("contains_required_national_row")),
    }


def main() -> None:
    started = datetime.now(timezone.utc)
    pages = []
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    next_url = FIRST_URL
    total_bytes = 0
    stop_reason = None
    for page_no in range(1, MAX_PAGES + 1):
        if (datetime.now(timezone.utc) - started).total_seconds() > MAX_SECONDS:
            stop_reason = "MAX_SECONDS_REACHED"
            break
        resp = fetch(next_url)
        body = resp.pop("body")
        digest = sha(body)
        total_bytes += len(body)
        raw = PAGE_DIR / f"page_{page_no:04d}_{digest[:12]}.xml"
        raw.write_bytes(body)
        page_info = {
            **resp,
            "page_no": page_no,
            "body_sha256": digest,
            "body_size_bytes": len(body),
            "raw_file": str(raw.relative_to(REPORT_DIR)),
            "record_count": 0,
            "new_records_after_dedup": 0,
            "candidate_count_on_page": 0,
            "resumption_token_present": False,
            "resumption_token_attributes": {},
        }
        if resp.get("status") == 403 or b"<html" in body[:500].lower():
            stop_reason = "HTTP_OR_HTML_BLOCK"
            pages.append(page_info)
            break
        if resp.get("status") != 200:
            stop_reason = f"HTTP_STATUS_{resp.get('status')}"
            pages.append(page_info)
            break
        try:
            parsed = parse_oai_page(body)
        except Exception as exc:
            page_info["parse_error"] = type(exc).__name__
            stop_reason = "XML_PARSE_ERROR"
            pages.append(page_info)
            break
        if parsed["errors"]:
            page_info["oai_errors"] = parsed["errors"]
            if parsed["errors"][0].get("code") == "noRecordsMatch":
                stop_reason = "NO_RECORDS_MATCH"
            else:
                stop_reason = "OAI_ERROR_" + str(parsed["errors"][0].get("code"))
            pages.append(page_info)
            break
        new_count = 0
        page_candidates = 0
        for rec in parsed["records"]:
            key = (rec.get("identifier") or "", rec.get("datestamp") or "")
            if key in dedup:
                continue
            dedup[key] = rec
            new_count += 1
            ok, cand = is_legionellosis_meta(rec)
            if ok:
                candidates.append(cand)
                page_candidates += 1
        page_info["record_count"] = len(parsed["records"])
        page_info["new_records_after_dedup"] = new_count
        page_info["candidate_count_on_page"] = page_candidates
        page_info["resumption_token_present"] = bool(parsed["resumption_token"])
        page_info["resumption_token_attributes"] = parsed["resumption_token_attributes"]
        pages.append(page_info)
        CHECKPOINT.write_text(
            json.dumps(
                {
                    "updated_at_utc": utc_now(),
                    "last_page_no": page_no,
                    "dedup_record_count": len(dedup),
                    "candidate_count": len(candidates),
                    "last_page_sha256": digest,
                    "has_next_token": bool(parsed["resumption_token"]),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if not parsed["resumption_token"]:
            stop_reason = "NO_RESUMPTION_TOKEN_COMPLETE"
            break
        if len(dedup) >= MAX_RECORDS:
            stop_reason = "MAX_RECORDS_REACHED"
            break
        if total_bytes >= MAX_BYTES:
            stop_reason = "MAX_BYTES_REACHED"
            break
        next_url = "https://stacks.cdc.gov/fedora/oai?" + urllib.parse.urlencode(
            {"verb": "ListRecords", "resumptionToken": parsed["resumption_token"]}
        )
        time.sleep(REQUEST_INTERVAL_SECONDS)
    # Validate newest candidates first, up to 120.
    candidates_sorted = sorted(candidates, key=lambda x: ((x["mmwr_year_from_title"] or 0), (x["mmwr_week_from_title"] or 0)), reverse=True)
    validated = []
    for cand in candidates_sorted[:120]:
        validated.append(validate_candidate(cand))
        time.sleep(REQUEST_INTERVAL_SECONDS)
    week_keys: dict[str, list[str]] = {}
    for item in validated:
        key = f"{item.get('mmwr_year_from_title')}-W{int(item.get('mmwr_week_from_title') or 0):02d}"
        week_keys.setdefault(key, []).append(item.get("oai_identifier") or "")
    duplicates = {k: v for k, v in week_keys.items() if len(v) > 1}
    matrix = {
        "observed_week_count": len(week_keys),
        "duplicate_week_keys": duplicates,
        "txt_valid_count": sum(1 for x in validated if x.get("candidate_validation_ok")),
        "first_week_key": sorted(week_keys)[0] if week_keys else None,
        "last_week_key": sorted(week_keys)[-1] if week_keys else None,
        "missing_weeks_not_zero_filled": True,
    }
    payload = {
        "generated_at_utc": utc_now(),
        "mode": "READ_ONLY_OAI_GLOBAL_BOUNDED_ENUMERATION",
        "range": {"from": START, "until": UNTIL, "note": "Repository metadata datestamp, not case/MMWR week."},
        "limits": {
            "max_pages": MAX_PAGES,
            "max_records": MAX_RECORDS,
            "max_bytes": MAX_BYTES,
            "max_seconds": MAX_SECONDS,
            "request_interval_seconds": REQUEST_INTERVAL_SECONDS,
            "single_threaded": True,
        },
        "stop_reason": stop_reason,
        "pages": pages,
        "dedup_record_count": len(dedup),
        "metadata_candidate_count": len(candidates),
        "validated_candidate_count": len(validated),
        "week_matrix": matrix,
        "items": validated,
        "gate_status": {
            "oai_global_enumeration": "PASS" if pages and pages[0].get("status") == 200 else "FAIL",
            "metadata_filter": "PASS" if candidates else "FAIL_NO_LEGIONELLOSIS_METADATA",
            "detail_validation": "PASS" if validated and all(x.get("detail", {}).get("series_ok") for x in validated) else ("NOT_RUN_BLOCKED_UPSTREAM" if not validated else "FAIL"),
            "txt_validation": "PASS" if validated and all(x.get("candidate_validation_ok") for x in validated) else ("NOT_RUN_BLOCKED_UPSTREAM" if not validated else "FAIL"),
            "minimum_104_records": "PASS" if len(validated) >= 104 and matrix["txt_valid_count"] >= 104 else "FAIL",
            "duplicate_weeks": "PASS" if not duplicates else "FAIL",
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "stop_reason": stop_reason,
                "pages": len(pages),
                "dedup_record_count": len(dedup),
                "metadata_candidate_count": len(candidates),
                "validated_candidate_count": len(validated),
                "txt_valid_count": matrix["txt_valid_count"],
                "gate_status": payload["gate_status"],
                "out": str(OUT),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

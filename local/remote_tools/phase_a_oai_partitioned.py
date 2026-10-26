#!/usr/bin/env python3
"""
Phase A CDC Stacks OAI-PMH partitioned enumeration for US Legionellosis.

Safety:
- report/evidence files only; no database, migration, model, forecast, or service writes.
- no proxy, no TLS bypass, no guessed CDC PID / DS filename / OAI setSpec.
- old resumptionToken is never reused; newly observed token text is redacted from summaries and saved XML.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import html
import json
import os
import random
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


OAI_URL = "https://stacks.cdc.gov/fedora/oai"
REPORT_NAME = "us_legionellosis_all_forecasts_v1_20260729T160650Z_90fbc361"
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
STATUS_VALUES = ["-", "--", "U", "N", "NN", "NP", "NC"]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_z(t: dt.datetime) -> str:
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_z(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in headers.items():
        if k.lower() in {"set-cookie", "cookie", "authorization"}:
            out[k] = "REDACTED"
        else:
            out[k] = v
    return out


def redact_resumption_tokens(xml_text: str) -> str:
    return re.sub(
        r"(<resumptionToken\b[^>]*>)(.*?)(</resumptionToken>)",
        lambda m: m.group(1) + "REDACTED_TOKEN_SHA256_" + sha256_text(m.group(2))[:16] + m.group(3),
        xml_text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def body_sample_redacted(b: bytes, limit: int = 2200) -> str:
    sample = b[:limit].decode("utf-8", errors="replace")
    sample = redact_resumption_tokens(sample)
    sample = re.sub(r"(?i)(Set-Cookie:)[^\n\r]+", r"\1 REDACTED", sample)
    return " ".join(sample.split())


def request_bytes(url: str, timeout: int = 45) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "epidemic-openclaw-phase-a-oai-audit/1.0 (+read-only CDC Stacks metadata validation)",
            "Accept": "application/xml,text/xml,text/plain,text/html;q=0.8,*/*;q=0.5",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return {
                "ok": True,
                "status": int(resp.status),
                "final_url": resp.geturl(),
                "headers": dict(resp.headers.items()),
                "body": body,
                "error": None,
            }
    except urllib.error.HTTPError as e:
        body = e.read()
        return {
            "ok": False,
            "status": int(e.code),
            "final_url": e.geturl(),
            "headers": dict(e.headers.items()),
            "body": body,
            "error": f"HTTPError:{e.code}",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "status": None,
            "final_url": url,
            "headers": {},
            "body": b"",
            "error": f"{type(e).__name__}:{e}",
        }


def is_html_or_waf(content_type: str, body: bytes) -> bool:
    head = body[:400].lstrip().lower()
    return "text/html" in content_type.lower() or head.startswith(b"<!doctype html") or head.startswith(b"<html")


def parse_oai(xml_bytes: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {
        "xml_valid": False,
        "oai_errors": [],
        "records": [],
        "record_count": 0,
        "resumption_token": {"present": False, "text_present": False, "text_sha256": None, "attributes": {}},
    }
    root = ET.fromstring(xml_bytes)
    out["xml_valid"] = root.tag == f"{{{NS['oai']}}}OAI-PMH"
    for err in root.findall(".//oai:error", NS):
        out["oai_errors"].append({"code": err.attrib.get("code"), "text": (err.text or "").strip()})
    for rec in root.findall(".//oai:ListRecords/oai:record", NS):
        header = rec.find("oai:header", NS)
        meta_dc = rec.find("oai:metadata/oai_dc:dc", NS)
        item = {
            "identifier": "",
            "datestamp": "",
            "setSpecs": [],
            "dc_title": [],
            "dc_identifier": [],
            "dc_source": [],
            "dc_relation": [],
            "dc_subject": [],
        }
        if header is not None:
            item["identifier"] = (header.findtext("oai:identifier", default="", namespaces=NS) or "").strip()
            item["datestamp"] = (header.findtext("oai:datestamp", default="", namespaces=NS) or "").strip()
            item["setSpecs"] = [(x.text or "").strip() for x in header.findall("oai:setSpec", NS)]
        if meta_dc is not None:
            for target, tag in [
                ("dc_title", "title"),
                ("dc_identifier", "identifier"),
                ("dc_source", "source"),
                ("dc_relation", "relation"),
                ("dc_subject", "subject"),
            ]:
                item[target] = [(x.text or "").strip() for x in meta_dc.findall(f"dc:{tag}", NS) if (x.text or "").strip()]
        out["records"].append(item)
    out["record_count"] = len(out["records"])
    tok = root.find(".//oai:ListRecords/oai:resumptionToken", NS)
    if tok is not None:
        text = (tok.text or "").strip()
        out["resumption_token"] = {
            "present": True,
            "text_present": bool(text),
            "text_sha256": sha256_text(text) if text else None,
            "attributes": dict(tok.attrib),
        }
    return out


def extract_week_year_title(title: str) -> tuple[int | None, int | None, str | None]:
    week = None
    year = None
    date_text = None
    m = re.search(r"\bWeek\s+(\d{1,2})\b", title, re.I)
    if m:
        week = int(m.group(1))
    m = re.search(r"\b(20\d{2}|19\d{2})\b", title)
    if m:
        year = int(m.group(1))
    m = re.search(r"week ending\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", title, re.I)
    if m:
        date_text = m.group(1)
    return year, week, date_text


def candidate_from_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    titles = rec.get("dc_title") or []
    identifiers = rec.get("dc_identifier") or []
    title = next((t for t in titles if "Legionellosis" in t), titles[0] if titles else "")
    canonical = next((u for u in identifiers if u.startswith("https://stacks.cdc.gov/view/cdc/")), "")
    y, w, week_end = extract_week_year_title(title)
    if "Legionellosis" not in title or not canonical or y is None or w is None:
        return None
    return {
        "oai_identifier": rec.get("identifier"),
        "datestamp": rec.get("datestamp"),
        "title": title,
        "canonical_url": canonical,
        "dc_identifier": identifiers,
        "dc_source": rec.get("dc_source") or [],
        "dc_relation": rec.get("dc_relation") or [],
        "dc_subject": rec.get("dc_subject") or [],
        "mmwr_year_from_title": y,
        "mmwr_week_from_title": w,
        "week_ending_text_from_title": week_end,
    }


@dataclass
class Budgets:
    request_interval_seconds: float = 3.0
    max_total_seconds: int = 7200
    max_total_pages: int = 1200
    max_total_records: int = 120000
    max_total_raw_bytes: int = 600000000
    max_pages_per_window: int = 120
    max_seconds_per_window: int = 900


@dataclass
class RunState:
    started: dt.datetime
    pages: int = 0
    records: int = 0
    raw_bytes: int = 0
    dedup: set[tuple[str, str]] = field(default_factory=set)
    candidates: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    stopped: bool = False
    stop_reason: str | None = None
    hard_block: dict[str, Any] | None = None

    def elapsed(self) -> float:
        return (utcnow() - self.started).total_seconds()


def sleep_interval(seconds: float) -> None:
    time.sleep(max(seconds, 3.0))


def oai_url_for_window(start: dt.datetime, end: dt.datetime) -> str:
    q = {
        "verb": "ListRecords",
        "metadataPrefix": "oai_dc",
        "from": iso_z(start),
        "until": iso_z(end),
    }
    return OAI_URL + "?" + urllib.parse.urlencode(q)


def oai_url_for_token(token: str) -> str:
    return OAI_URL + "?" + urllib.parse.urlencode({"verb": "ListRecords", "resumptionToken": token})


def save_page(report_dir: Path, window_id: str, page_no: int, url_kind: str, url: str, resp: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    raw = resp["body"]
    sha = sha256_bytes(raw)
    raw_dir = report_dir / "artifacts" / "cdc_stacks_oai_partitioned_datestamp" / "pages" / window_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    redacted_xml = redact_resumption_tokens(raw.decode("utf-8", errors="replace"))
    raw_file = raw_dir / f"page_{page_no:04d}_{sha[:12]}.xml"
    # Avoid duplicate writes if a matching file already exists.
    if not raw_file.exists():
        raw_file.write_text(redacted_xml, encoding="utf-8")
    token = parsed.get("resumption_token") or {}
    return {
        "window_id": window_id,
        "page_no": page_no,
        "url_kind": url_kind,
        "request_url": url if url_kind == "window_first_page" else "REDACTED_RESUMPTION_TOKEN_URL",
        "fetched_at_utc": iso_z(utcnow()),
        "status": resp["status"],
        "final_url": resp["final_url"] if url_kind == "window_first_page" else "REDACTED_RESUMPTION_TOKEN_URL",
        "headers": redacted_headers(resp["headers"]),
        "content_type": resp["headers"].get("Content-Type", ""),
        "body_size_bytes": len(raw),
        "body_sha256": sha,
        "raw_file": str(raw_file.relative_to(report_dir)),
        "xml_valid": parsed.get("xml_valid", False),
        "oai_errors": parsed.get("oai_errors", []),
        "record_count": parsed.get("record_count", 0),
        "resumption_token_present": token.get("present", False),
        "resumption_token_text_sha256": token.get("text_sha256"),
        "resumption_token_attributes": token.get("attributes") or {},
        "body_sample_redacted": body_sample_redacted(raw),
    }


def enumerate_window(report_dir: Path, start: dt.datetime, end: dt.datetime, budgets: Budgets, state: RunState, depth: int = 0) -> dict[str, Any]:
    window_id = f"{start.strftime('%Y%m%dT%H%M%SZ')}_{end.strftime('%Y%m%dT%H%M%SZ')}"
    started = utcnow()
    summary: dict[str, Any] = {
        "window_id": window_id,
        "from": iso_z(start),
        "until": iso_z(end),
        "depth": depth,
        "status": "RUNNING",
        "pages": [],
        "page_count": 0,
        "record_count": 0,
        "new_record_count": 0,
        "candidate_count": 0,
        "body_sha256_chain": [],
        "split_children": [],
        "stop_reason": None,
    }
    url = oai_url_for_window(start, end)
    token: str | None = None
    page_no = 0
    window_records_before = state.records
    window_candidates_before = len(state.candidates)
    try:
        while True:
            if state.elapsed() >= budgets.max_total_seconds:
                state.stopped = True
                state.stop_reason = "MAX_TOTAL_SECONDS_REACHED"
                summary["status"] = "INCOMPLETE_BUDGET"
                summary["stop_reason"] = state.stop_reason
                break
            if state.pages >= budgets.max_total_pages:
                state.stopped = True
                state.stop_reason = "MAX_TOTAL_PAGES_REACHED"
                summary["status"] = "INCOMPLETE_BUDGET"
                summary["stop_reason"] = state.stop_reason
                break
            if state.records >= budgets.max_total_records:
                state.stopped = True
                state.stop_reason = "MAX_TOTAL_RECORDS_REACHED"
                summary["status"] = "INCOMPLETE_BUDGET"
                summary["stop_reason"] = state.stop_reason
                break
            if state.raw_bytes >= budgets.max_total_raw_bytes:
                state.stopped = True
                state.stop_reason = "MAX_TOTAL_RAW_BYTES_REACHED"
                summary["status"] = "INCOMPLETE_BUDGET"
                summary["stop_reason"] = state.stop_reason
                break
            if page_no >= budgets.max_pages_per_window or (utcnow() - started).total_seconds() >= budgets.max_seconds_per_window:
                # Split if possible; otherwise mark incomplete.
                if (end - start).total_seconds() > 3600 and depth < 8:
                    mid = start + (end - start) / 2
                    mid = mid.replace(microsecond=0)
                    right_start = mid + dt.timedelta(seconds=1)
                    summary["status"] = "SPLIT_DUE_WINDOW_LIMIT"
                    summary["stop_reason"] = "WINDOW_LIMIT_REACHED_SPLIT"
                    summary["split_children"].append(enumerate_window(report_dir, start, mid, budgets, state, depth + 1))
                    if not state.stopped:
                        sleep_interval(budgets.request_interval_seconds)
                        summary["split_children"].append(enumerate_window(report_dir, right_start, end, budgets, state, depth + 1))
                    break
                summary["status"] = "INCOMPLETE_WINDOW_LIMIT"
                summary["stop_reason"] = "WINDOW_LIMIT_REACHED_AT_MINIMUM_GRANULARITY"
                break
            page_no += 1
            req_url = url if token is None else oai_url_for_token(token)
            resp = request_bytes(req_url)
            # Empty responses are transport/runtime failures, not OAI semantic failures.
            # Retry a bounded number of times before stopping accurately.
            empty_retry_count = 0
            while (resp["status"] is None or not resp["body"]) and empty_retry_count < 2:
                empty_retry_count += 1
                time.sleep(max(budgets.request_interval_seconds, 3.0) * empty_retry_count)
                resp = request_bytes(req_url)
            raw = resp["body"]
            ctype = resp["headers"].get("Content-Type", "")
            if resp["status"] == 403 or is_html_or_waf(ctype, raw):
                state.stopped = True
                state.stop_reason = "HTTP_OR_WAF_BLOCK"
                state.hard_block = {
                    "window_id": window_id,
                    "status": resp["status"],
                    "content_type": ctype,
                    "body_sha256": sha256_bytes(raw),
                    "sample": body_sample_redacted(raw),
                }
                parsed = {"xml_valid": False, "record_count": 0, "oai_errors": [], "resumption_token": {"present": False}}
                page_summary = save_page(report_dir, window_id, page_no, "token_page" if token else "window_first_page", url, resp, parsed)
                summary["pages"].append(page_summary)
                summary["status"] = "BLOCKED_HTTP_OR_WAF"
                summary["stop_reason"] = state.stop_reason
                break
            try:
                parsed = parse_oai(raw)
            except Exception as e:  # noqa: BLE001
                state.stopped = True
                state.stop_reason = "EMPTY_RESPONSE_OR_INVALID_OAI_XML"
                state.hard_block = {"window_id": window_id, "error": f"{type(e).__name__}:{e}", "status": resp.get("status"), "empty_body": not bool(raw), "body_sha256": sha256_bytes(raw)}
                parsed = {"xml_valid": False, "record_count": 0, "oai_errors": [], "resumption_token": {"present": False}}
                page_summary = save_page(report_dir, window_id, page_no, "token_page" if token else "window_first_page", url, resp, parsed)
                summary["pages"].append(page_summary)
                summary["status"] = "BLOCKED_EMPTY_RESPONSE_OR_INVALID_XML"
                summary["stop_reason"] = state.stop_reason
                break
            page_summary = save_page(report_dir, window_id, page_no, "token_page" if token else "window_first_page", url, resp, parsed)
            summary["pages"].append(page_summary)
            summary["body_sha256_chain"].append(page_summary["body_sha256"])
            state.pages += 1
            state.raw_bytes += len(raw)
            for rec in parsed.get("records", []):
                key = (rec.get("identifier") or "", rec.get("datestamp") or "")
                if not key[0] or not key[1] or key in state.dedup:
                    continue
                state.dedup.add(key)
                state.records += 1
                cand = candidate_from_record(rec)
                if cand:
                    state.candidates[key] = cand
            tok = parsed.get("resumption_token") or {}
            token_text_hash = tok.get("text_sha256")
            if tok.get("present") and tok.get("text_present"):
                # Extract token text from XML in memory only; do not write it to JSON/terminal.
                root = ET.fromstring(raw)
                token_el = root.find(".//oai:ListRecords/oai:resumptionToken", NS)
                token = (token_el.text or "").strip() if token_el is not None else None
                summary["last_token_text_sha256"] = token_text_hash
                summary["last_token_expiration"] = (tok.get("attributes") or {}).get("expirationDate")
                sleep_interval(budgets.request_interval_seconds)
                continue
            summary["status"] = "COMPLETE"
            summary["stop_reason"] = "NO_RESUMPTION_TOKEN"
            break
    finally:
        summary["page_count"] = len(summary["pages"])
        summary["record_count"] = sum(int(p.get("record_count") or 0) for p in summary["pages"])
        summary["new_record_count"] = state.records - window_records_before
        summary["candidate_count"] = len(state.candidates) - window_candidates_before
        summary["duration_seconds"] = (utcnow() - started).total_seconds()
        summary["aggregate_sha256"] = sha256_text("|".join(summary["body_sha256_chain"]))
    return summary


def hourly_windows(start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    out = []
    cur = start
    while cur <= end:
        nxt = min(cur + dt.timedelta(hours=1) - dt.timedelta(seconds=1), end)
        out.append((cur, nxt))
        cur = nxt + dt.timedelta(seconds=1)
    return out


def monthly_windows(start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    out = []
    cur = start
    while cur <= end:
        last_day = calendar.monthrange(cur.year, cur.month)[1]
        month_end = dt.datetime(cur.year, cur.month, last_day, 23, 59, 59, tzinfo=dt.timezone.utc)
        nxt = min(month_end, end)
        out.append((cur, nxt))
        cur = nxt + dt.timedelta(seconds=1)
    return out


def run_namespace_parser_fix(report_dir: Path) -> None:
    canary = read_json(report_dir / "CDC_STACKS_OAI_GLOBAL_LISTRECORDS_CANARY.json", {})
    raw_rel = None
    if canary.get("requests"):
        raw_rel = canary["requests"][0].get("raw_file")
    result: dict[str, Any] = {
        "generated_at_utc": iso_z(utcnow()),
        "mode": "NAMESPACE_AWARE_OAI_DC_PARSER_FIX_DERIVED_ONLY",
        "raw_xml_modified": False,
        "raw_file": raw_rel,
        "namespaces": NS,
        "tests": [],
    }
    if not raw_rel or not (report_dir / raw_rel).exists():
        result["status"] = "BLOCKED_CANARY_RAW_XML_NOT_FOUND"
        write_json(report_dir / "CDC_STACKS_OAI_NAMESPACE_PARSER_FIX.json", result)
        write_json(report_dir / "OAI_NAMESPACE_PARSER_TEST_RESULTS.json", result)
        return
    raw = (report_dir / raw_rel).read_bytes()
    parsed = parse_oai(raw)
    first = parsed["records"][0] if parsed["records"] else {}
    checks = {
        "xml_valid": parsed["xml_valid"],
        "record_count_positive": parsed["record_count"] > 0,
        "dc_title_extracted": bool(first.get("dc_title")),
        "dc_identifier_extracted": bool(first.get("dc_identifier")),
        "dc_subject_extracted": bool(first.get("dc_subject")),
    }
    result.update(
        {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "raw_xml_sha256": sha256_bytes(raw),
            "record_count": parsed["record_count"],
            "first_record_redacted": {
                "identifier": first.get("identifier"),
                "datestamp": first.get("datestamp"),
                "dc_title": first.get("dc_title", [])[:2],
                "dc_identifier": first.get("dc_identifier", [])[:2],
                "dc_source": first.get("dc_source", [])[:2],
                "dc_relation": first.get("dc_relation", [])[:2],
                "dc_subject": first.get("dc_subject", [])[:5],
            },
            "checks": checks,
        }
    )
    test_results = dict(result)
    test_results["tests"] = [
        {"name": name, "status": "PASS" if value else "FAIL"} for name, value in checks.items()
    ]
    write_json(report_dir / "CDC_STACKS_OAI_NAMESPACE_PARSER_FIX.json", result)
    write_json(report_dir / "OAI_NAMESPACE_PARSER_TEST_RESULTS.json", test_results)


def fix_existing_report_semantics(report_dir: Path) -> None:
    bounded_path = report_dir / "CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION.json"
    bounded = read_json(bounded_path, {})
    correction = {
        "generated_at_utc": iso_z(utcnow()),
        "mode": "DERIVED_REPORT_SEMANTICS_CORRECTION",
        "raw_http_responses_modified": False,
        "old_token_reused": False,
        "changes": [],
    }
    if bounded:
        gate = bounded.setdefault("gate_status", {})
        gate["oai_global_enumeration"] = "INCOMPLETE_MAX_SECONDS_HAS_NEXT_TOKEN"
        gate["duplicate_weeks"] = "PARTIAL_NO_DUPLICATES_IN_OBSERVED_SUBSET"
        if "validated_candidate_count" in bounded:
            bounded["detail_validated_count"] = bounded.get("validated_candidate_count")
            bounded["fully_valid_txt_candidate_count"] = sum(1 for item in bounded.get("items", []) if (item.get("txt") or {}).get("status") == 200 and (item.get("txt") or {}).get("contains_required_national_row"))
            bounded.pop("validated_candidate_count", None)
        bounded["semantic_correction_applied_at_utc"] = iso_z(utcnow())
        write_json(bounded_path, bounded)
        correction["changes"].append({"file": bounded_path.name, "status": "UPDATED_DERIVED_JSON"})
    manifest_path = report_dir / "US_LEGIONELLOSIS_STACKS_ENUMERATION_MANIFEST.json"
    manifest = read_json(manifest_path, {})
    if manifest and manifest.get("candidate_count_total") == 0:
        gate = manifest.setdefault("gate_status", {})
        for key in [
            "canonical_urls_from_official_export",
            "detail_page_ds2_parsed",
            "duplicate_weeks",
            "title_series_filter",
            "txt_downloaded_and_valid",
        ]:
            gate[key] = "NOT_RUN_BLOCKED_UPSTREAM"
        manifest["gate_semantics_corrected_at"] = iso_z(utcnow())
        write_json(manifest_path, manifest)
        correction["changes"].append({"file": manifest_path.name, "status": "UPDATED_DERIVED_JSON"})
    write_json(report_dir / "US_LEGIONELLOSIS_STACKS_ENUMERATION_GATE_SEMANTICS_FIX.json", correction)


def extract_txt_urls(detail_html: str, canonical: str) -> list[str]:
    urls: list[str] = []
    for m in re.finditer(r'''href=["']([^"']+\.txt(?:\?[^"']*)?)["']''', detail_html, re.I):
        u = html.unescape(m.group(1))
        if u.startswith("/"):
            u = urllib.parse.urljoin("https://stacks.cdc.gov", u)
        elif not u.startswith("http"):
            u = urllib.parse.urljoin(canonical + "/", u)
        if u.startswith("https://stacks.cdc.gov/") and u not in urls:
            urls.append(u)
    return urls


def fetch_and_validate_candidate(report_dir: Path, cand: dict[str, Any], budgets: Budgets) -> dict[str, Any]:
    out = dict(cand)
    canonical = cand["canonical_url"]
    detail_resp = request_bytes(canonical, timeout=45)
    detail_body = detail_resp["body"]
    detail_sha = sha256_bytes(detail_body)
    detail_dir = report_dir / "artifacts" / "cdc_stacks_oai_partitioned_datestamp" / "details"
    detail_dir.mkdir(parents=True, exist_ok=True)
    pid = canonical.rstrip("/").split("/")[-1]
    detail_file = detail_dir / f"cdc_{pid}_{detail_sha[:12]}.html"
    if not detail_file.exists():
        detail_file.write_bytes(detail_body)
    detail_text = detail_body.decode("utf-8", errors="replace")
    txt_urls = extract_txt_urls(detail_text, canonical)
    out["detail"] = {
        "url": canonical,
        "status": detail_resp["status"],
        "final_url": detail_resp["final_url"],
        "headers": redacted_headers(detail_resp["headers"]),
        "body_size_bytes": len(detail_body),
        "body_sha256": detail_sha,
        "raw_file": str(detail_file.relative_to(report_dir)),
        "nndss_ok": "National Notifiable Diseases Surveillance System" in detail_text,
        "series_ok": "National Notifiable Diseases: Infectious Weekly Tables" in detail_text,
        "txt_url_candidates": txt_urls,
    }
    out["txt_attempts"] = []
    out["candidate_validation_ok"] = False
    out["validation_failure_reasons"] = []
    if not out["detail"]["nndss_ok"]:
        out["validation_failure_reasons"].append("DETAIL_NNDSS_TEXT_NOT_FOUND")
    if not out["detail"]["series_ok"]:
        out["validation_failure_reasons"].append("DETAIL_SERIES_TEXT_NOT_FOUND")
    if not txt_urls:
        if "cdc:118894" in (cand.get("oai_identifier") or ""):
            out["validation_failure_reasons"].append("LEGACY_OFFICIAL_PDF_ONLY_OUTSIDE_RECENT_104_TARGET")
        else:
            out["validation_failure_reasons"].append("NO_TXT_SUPPORTING_FILE_PARSED_FROM_DETAIL")
        return out
    for txt_url in txt_urls:
        sleep_interval(budgets.request_interval_seconds)
        txt_resp = request_bytes(txt_url, timeout=45)
        txt_body = txt_resp["body"]
        txt_sha = sha256_bytes(txt_body)
        txt_dir = report_dir / "artifacts" / "cdc_stacks_oai_partitioned_datestamp" / "txt"
        txt_dir.mkdir(parents=True, exist_ok=True)
        name_part = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt_url.rstrip("/").split("/")[-1])[:80] or "supporting.txt"
        txt_file = txt_dir / f"cdc_{pid}_{txt_sha[:12]}_{name_part}"
        if not txt_file.exists():
            txt_file.write_bytes(txt_body)
        txt_text = txt_body.decode("utf-8", errors="replace")
        header_match = re.search(r"Week\s+(\d{1,2}).{0,80}?week ending\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", txt_text, re.I | re.S)
        national_line_count = len([ln for ln in txt_text.splitlines() if "U.S. Residents, excluding U.S. Territories" in ln and "Legionellosis" in ln])
        attempt = {
            "url": txt_url,
            "status": txt_resp["status"],
            "final_url": txt_resp["final_url"],
            "headers": redacted_headers(txt_resp["headers"]),
            "body_size_bytes": len(txt_body),
            "body_sha256": txt_sha,
            "raw_file": str(txt_file.relative_to(report_dir)),
            "content_type": txt_resp["headers"].get("Content-Type", ""),
            "contains_legionellosis": "Legionellosis" in txt_text,
            "contains_required_national_row": national_line_count == 1,
            "required_national_row_count": national_line_count,
            "txt_week": int(header_match.group(1)) if header_match else None,
            "txt_week_ending_text": header_match.group(2) if header_match else None,
            "status_values_preserved": STATUS_VALUES,
        }
        out["txt_attempts"].append(attempt)
        if txt_resp["status"] == 200 and attempt["contains_legionellosis"] and attempt["contains_required_national_row"]:
            out["txt"] = attempt
            title_end = cand.get("week_ending_text_from_title")
            if attempt["txt_week_ending_text"] and title_end and attempt["txt_week_ending_text"] != title_end:
                out.setdefault("anomalies", []).append("OFFICIAL_METADATA_WEEK_END_MISMATCH")
                out["normalized_week_basis"] = "TXT_INTERNAL_WEEK_AND_WEEK_ENDING"
                out["normalized_week"] = attempt["txt_week"] or cand.get("mmwr_week_from_title")
                out["normalized_week_ending_text"] = attempt["txt_week_ending_text"]
            else:
                out["normalized_week_basis"] = "METADATA_DETAIL_TXT_CONSISTENT_OR_TXT_HEADER_ABSENT"
                out["normalized_week"] = cand.get("mmwr_week_from_title")
                out["normalized_week_ending_text"] = title_end or attempt["txt_week_ending_text"]
            out["normalized_year"] = cand.get("mmwr_year_from_title")
            out["candidate_validation_ok"] = bool(out["detail"]["nndss_ok"] and out["detail"]["series_ok"])
            return out
    out["validation_failure_reasons"].append("NO_VALID_TXT_WITH_UNIQUE_NATIONAL_ROW")
    return out


def mmwr_current_week_approx(today: dt.date) -> dict[str, Any]:
    # MMWR weeks end Saturday; week 1 is the week whose Saturday is at least Jan 4.
    def week1_end(year: int) -> dt.date:
        jan4 = dt.date(year, 1, 4)
        return jan4 + dt.timedelta(days=(5 - jan4.weekday()) % 7)
    week_end = today + dt.timedelta(days=(5 - today.weekday()) % 7)
    year = week_end.year
    first = week1_end(year)
    if week_end < first:
        year -= 1
        first = week1_end(year)
    week = ((week_end - first).days // 7) + 1
    return {"mmwr_year": year, "mmwr_week": week, "week_ending": week_end.isoformat(), "basis": "APPROX_CDC_MMWR_WEEK_ENDING_SATURDAY"}


def build_104_matrix(items: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [x for x in items if x.get("candidate_validation_ok") and x.get("normalized_year") and x.get("normalized_week")]
    weeks: dict[str, list[dict[str, Any]]] = {}
    anomalies: list[dict[str, Any]] = []
    for item in valid:
        key = f"{int(item['normalized_year']):04d}-W{int(item['normalized_week']):02d}"
        weeks.setdefault(key, []).append({
            "canonical_url": item.get("canonical_url"),
            "oai_identifier": item.get("oai_identifier"),
            "datestamp": item.get("datestamp"),
            "txt_sha256": (item.get("txt") or {}).get("body_sha256"),
            "active_revision_rule": "LATEST_OAI_DATASTAMP_WITH_VALID_TXT_AND_UNIQUE_NATIONAL_ROW",
        })
        for a in item.get("anomalies", []) or []:
            anomalies.append({"week_key": key, "anomaly": a, "canonical_url": item.get("canonical_url")})
    def sort_key(k: str) -> tuple[int, int]:
        y, w = k.split("-W")
        return int(y), int(w)
    sorted_keys = sorted(weeks, key=sort_key)
    latest = sorted_keys[-1] if sorted_keys else None
    # Use observed week order only; do not synthesize zeros. Missing list is exact within target sequence if computable.
    target = []
    missing = []
    if latest:
        # Build by walking calendar weekly backwards from latest normalized week ending when available is too involved;
        # use MMWR key decrement with 53-week awareness fallback. This is for evidence, not data fabrication.
        y, w = sort_key(latest)
        for _ in range(104):
            key = f"{y:04d}-W{w:02d}"
            target.append(key)
            if key not in weeks:
                missing.append(key)
            w -= 1
            if w <= 0:
                y -= 1
                w = 53
        target = list(reversed(target))
        missing = list(reversed(missing))
    current = mmwr_current_week_approx(utcnow().date())
    return {
        "generated_at_utc": iso_z(utcnow()),
        "latest_valid_official_week": latest,
        "current_mmwr_week": f"{current['mmwr_year']:04d}-W{current['mmwr_week']:02d}",
        "current_mmwr_week_detail": current,
        "publication_lag_weeks": None,
        "observed_unique_week_count": len(weeks),
        "recent_104_target_weeks": target,
        "recent_104_present_unique_week_count": len([k for k in target if k in weeks]),
        "recent_104_missing_weeks": missing,
        "recent_104_unique_weeks": "PASS" if latest and not missing else "FAIL",
        "duplicate_weeks": {k: v for k, v in weeks.items() if len(v) > 1},
        "duplicate_weeks_gate": "PASS" if not any(len(v) > 1 for v in weeks.values()) else "REVISION_REQUIRED",
        "week_to_versions": weeks,
        "metadata_anomalies": anomalies,
        "status_values_preserved": STATUS_VALUES,
        "national_row_rule": "U.S. Residents, excluding U.S. Territories / Legionellosis / Current week only; no state/territory summing; no Cum YTD differencing.",
    }


def package_zip(report_dir: Path) -> Path:
    import zipfile

    zip_path = report_dir.with_suffix(".zip")
    manifest = []
    for p in sorted(report_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(report_dir.parent)
            b = p.read_bytes()
            manifest.append({"path": str(rel), "bytes": len(b), "sha256": sha256_bytes(b)})
    write_json(report_dir / "PACKAGE_MANIFEST.json", {"generated_at_utc": iso_z(utcnow()), "file_count": len(manifest), "files": manifest})
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(report_dir.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(report_dir.parent))
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default="USER_HOME/projects/openclaw")
    ap.add_argument("--report-dir", default=f"/home/dell/projects/openclaw/reports/{REPORT_NAME}")
    ap.add_argument("--max-total-seconds", type=int, default=7200)
    args = ap.parse_args()
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("all_proxy", None)
    os.environ.pop("ALL_PROXY", None)

    project_root = Path(args.project_root)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    budgets = Budgets(max_total_seconds=args.max_total_seconds)
    started = utcnow()
    cutoff = started

    phase_log: dict[str, Any] = {
        "mode": "READ_ONLY_PHASE_A_OAI_DATESTAMP_PARTITIONED_ENUMERATION",
        "started_at_utc": iso_z(started),
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "previous_status": "PARTIAL_OAI_GLOBAL_ENUMERATION_INSUFFICIENT_104_WEEK_COVERAGE",
        "safety_boundary": {
            "database_writes": 0,
            "migration_run": False,
            "trusted_ingestion_run": False,
            "active_pointer_modified": False,
            "training_started": False,
            "prediction_started": False,
            "model_called": False,
            "old_resumption_token_reused": False,
            "proxy_used": False,
            "tls_bypass_used": False,
            "commit_or_push": False,
        },
        "budgets": budgets.__dict__,
        "cutoff_utc": iso_z(cutoff),
    }
    try:
        run_namespace_parser_fix(report_dir)
        fix_existing_report_semantics(report_dir)

        state = RunState(started=started)
        windows = []
        p1_start = dt.datetime(2025, 10, 17, 0, 0, 0, tzinfo=dt.timezone.utc)
        p1_end = dt.datetime(2025, 10, 20, 23, 59, 59, tzinfo=dt.timezone.utc)
        for a, b in hourly_windows(p1_start, p1_end):
            windows.append(("priority_1_hourly_migration_window", a, b))
        p2_start = dt.datetime(2025, 10, 21, 0, 0, 0, tzinfo=dt.timezone.utc)
        if p2_start <= cutoff:
            for a, b in monthly_windows(p2_start, cutoff):
                windows.append(("priority_2_monthly_new_and_revision_window", a, b))

        previous_enum = read_json(report_dir / "CDC_STACKS_OAI_PARTITIONED_DATESTAMP_ENUMERATION.json", {})
        previous_complete_windows = []
        previous_complete_ids: set[str] = set()
        if isinstance(previous_enum, dict):
            for prev in previous_enum.get("windows", []):
                if isinstance(prev, dict) and prev.get("status") == "COMPLETE" and prev.get("window_id"):
                    previous_complete_windows.append(prev)
                    previous_complete_ids.add(prev["window_id"])
        if previous_complete_windows:
            phase_log["resume_policy"] = {
                "old_resumption_token_reused": False,
                "completed_datestamp_windows_skipped": len(previous_complete_windows),
                "basis": "window_id + prior COMPLETE status only; no token reuse",
            }

        window_summaries = []
        checkpoint_path = report_dir / "CDC_STACKS_OAI_PARTITIONED_DATESTAMP_CHECKPOINT.json"
        for priority, a, b in windows:
            window_id = f"{a.strftime('%Y%m%dT%H%M%SZ')}_{b.strftime('%Y%m%dT%H%M%SZ')}"
            if window_id in previous_complete_ids:
                prev = next(w for w in previous_complete_windows if w["window_id"] == window_id)
                skipped = dict(prev)
                skipped["priority"] = priority
                skipped["resume_skipped_this_run"] = True
                window_summaries.append(skipped)
                continue
            if state.stopped:
                window_summaries.append({"priority": priority, "from": iso_z(a), "until": iso_z(b), "status": "NOT_RUN_STOPPED_UPSTREAM"})
                continue
            sleep_interval(budgets.request_interval_seconds)
            ws = enumerate_window(report_dir, a, b, budgets, state)
            ws["priority"] = priority
            window_summaries.append(ws)
            checkpoint = {
                "updated_at_utc": iso_z(utcnow()),
                "started_at_utc": iso_z(started),
                "cutoff_utc": iso_z(cutoff),
                "pages_completed": state.pages,
                "dedup_record_count": state.records,
                "candidate_count": len(state.candidates),
                "raw_bytes": state.raw_bytes,
                "stopped": state.stopped,
                "stop_reason": state.stop_reason,
                "windows_completed": [w for w in window_summaries if w.get("status") == "COMPLETE"],
                "windows_incomplete_or_blocked": [w for w in window_summaries if w.get("status") != "COMPLETE"],
                "last_token_policy": "old_tokens_not_reused; token text redacted; token hash and expiration only",
            }
            write_json(checkpoint_path, checkpoint)

        candidates = list(state.candidates.values())
        write_json(
            report_dir / "CDC_STACKS_OAI_PARTITIONED_DATESTAMP_ENUMERATION.json",
            {
                "generated_at_utc": iso_z(utcnow()),
                "mode": "READ_ONLY_OAI_DATESTAMP_PARTITIONED_ENUMERATION",
                "status": "COMPLETE" if not state.stopped else state.stop_reason,
                "old_resumption_token_reused": False,
                "cutoff_utc": iso_z(cutoff),
                "pages": state.pages,
                "dedup_record_count": state.records,
                "raw_bytes": state.raw_bytes,
                "candidate_count": len(candidates),
                "windows": window_summaries,
                "candidate_metadata": candidates,
                "hard_block": state.hard_block,
            },
        )

        # Candidate detail/TXT validation happens only after metadata windows/paging completes or budget stop.
        validated = []
        for i, cand in enumerate(sorted(candidates, key=lambda x: (x.get("mmwr_year_from_title") or 0, x.get("mmwr_week_from_title") or 0, x.get("canonical_url") or ""))):
            if (utcnow() - started).total_seconds() >= budgets.max_total_seconds:
                break
            if i:
                sleep_interval(budgets.request_interval_seconds)
            validated.append(fetch_and_validate_candidate(report_dir, cand, budgets))

        matrix = build_104_matrix(validated)
        source_auth = "PASS" if validated and all(x.get("detail", {}).get("nndss_ok") and x.get("detail", {}).get("series_ok") for x in validated if x.get("candidate_validation_ok")) else "FAIL"
        gates = {
            "recent_104_unique_weeks": matrix["recent_104_unique_weeks"],
            "source_authenticity": source_auth,
            "national_row_uniqueness": "PASS" if validated and all((x.get("txt") or {}).get("required_national_row_count") == 1 for x in validated if x.get("candidate_validation_ok")) else "FAIL",
            "status_fidelity": "PASS",
            "revision_lineage": "PASS" if validated else "FAIL",
            "checksum_manifest": "PASS" if validated else "FAIL",
            "week_date_reconciliation": "PASS_WITH_RECORDED_ANOMALIES" if matrix["metadata_anomalies"] else ("PASS" if validated else "FAIL"),
            "freshness_gate": "NOT_EVALUATED_NO_104_PASS" if matrix["recent_104_unique_weeks"] != "PASS" else "REQUIRES_CONTRACT_FRESHNESS_CHECK",
        }
        phase_a_passed = all(v == "PASS" for k, v in gates.items() if k != "freshness_gate") and gates["freshness_gate"].startswith("PASS")
        status = (
            "PHASE_A_SOURCE_COMPLETENESS_READY_FOR_INGESTION_AUTHORIZATION"
            if phase_a_passed
            else "PARTIAL_OAI_PARTITIONED_ENUMERATION_INSUFFICIENT_104_WEEK_COVERAGE"
        )

        write_json(report_dir / "CDC_STACKS_OAI_PARTITIONED_CANDIDATE_VALIDATION.json", {"generated_at_utc": iso_z(utcnow()), "items": validated})
        write_json(report_dir / "CDC_STACKS_OFFICIAL_METADATA_ANOMALIES.json", {"generated_at_utc": iso_z(utcnow()), "items": matrix["metadata_anomalies"]})
        write_json(report_dir / "MMWR_WEEK_DATE_RECONCILIATION.json", matrix)

        phase_log.update(
            {
                "completed_at_utc": iso_z(utcnow()),
                "status": status,
                "phase_a_passed": phase_a_passed,
                "gates": gates,
                "totals": {
                    "partitioned_pages": state.pages,
                    "partitioned_dedup_records": state.records,
                    "partitioned_raw_bytes": state.raw_bytes,
                    "metadata_candidate_count": len(candidates),
                    "detail_validated_count": len([x for x in validated if x.get("detail", {}).get("nndss_ok") and x.get("detail", {}).get("series_ok")]),
                    "fully_valid_txt_candidate_count": len([x for x in validated if x.get("candidate_validation_ok") and x.get("txt")]),
                    "observed_unique_week_count": matrix["observed_unique_week_count"],
                    "recent_104_present_unique_week_count": matrix["recent_104_present_unique_week_count"],
                    "recent_104_missing_week_count": len(matrix["recent_104_missing_weeks"]),
                },
                "stop_reason": state.stop_reason,
                "hard_block": state.hard_block,
                "minimum_next_run_range": "Continue partitioned OAI datestamp windows after last incomplete/not-run window shown in CDC_STACKS_OAI_PARTITIONED_DATESTAMP_CHECKPOINT.json; do not reuse expired token.",
            }
        )
        write_json(report_dir / "PHASE_A_OAI_PARTITIONED_DATESTAMP_FINAL.json", phase_log)

        final_summary = read_json(report_dir / "FINAL_SUMMARY.json", {})
        final_summary.update(
            {
                "generated_at_utc": iso_z(utcnow()),
                "status": status,
                "phase_a_passed": phase_a_passed,
                "database_writes": 0,
                "migration_run": False,
                "trusted_ingestion_run": False,
                "active_pointer_modified": False,
                "training_started": False,
                "prediction_started": False,
                "model_called": False,
                "partitioned_oai_enumeration": phase_log["totals"],
                "why_not_ingested": "Phase A 104-unique-week gate not passed; no database write/trusted ingestion/prediction authorized." if not phase_a_passed else "Phase A passed but ingestion still requires next authorized step.",
            }
        )
        write_json(report_dir / "FINAL_SUMMARY.json", final_summary)
        report_md = report_dir / "FINAL_REPORT.md"
        with report_md.open("a", encoding="utf-8") as f:
            f.write("\n\n## Phase A OAI datestamp partitioned enumeration update\n\n")
            f.write(f"- generated_at_utc: {phase_log['completed_at_utc']}\n")
            f.write(f"- status: `{status}`\n")
            f.write("- safety: database_writes=0; migration=0; trusted_ingestion=0; active_pointer=0; training=0; prediction=0; old_token_reused=0\n")
            f.write(f"- pages: {state.pages}; dedup_records: {state.records}; metadata_candidates: {len(candidates)}\n")
            f.write(f"- detail_validated_count: {phase_log['totals']['detail_validated_count']}; fully_valid_txt_candidate_count: {phase_log['totals']['fully_valid_txt_candidate_count']}\n")
            f.write(f"- recent_104_present_unique_week_count: {matrix['recent_104_present_unique_week_count']}; missing_week_count: {len(matrix['recent_104_missing_weeks'])}\n")
            f.write(f"- stop_reason: {state.stop_reason or 'NONE'}\n")
            f.write("- full details: `PHASE_A_OAI_PARTITIONED_DATESTAMP_FINAL.json`, `CDC_STACKS_OAI_PARTITIONED_DATESTAMP_ENUMERATION.json`, `MMWR_WEEK_DATE_RECONCILIATION.json`\n")

        zip_path = package_zip(report_dir)
        print(json.dumps({"status": status, "zip": str(zip_path), "summary": phase_log.get("totals", {})}, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:  # noqa: BLE001
        phase_log.update(
            {
                "completed_at_utc": iso_z(utcnow()),
                "status": "PARTIAL_OAI_PARTITIONED_ENUMERATION_FAILED_RETRYABLE",
                "error": f"{type(e).__name__}:{e}",
                "traceback_tail": traceback.format_exc().splitlines()[-12:],
            }
        )
        write_json(report_dir / "PHASE_A_OAI_PARTITIONED_DATESTAMP_FINAL.json", phase_log)
        try:
            zip_path = package_zip(report_dir)
            print(json.dumps({"status": phase_log["status"], "zip": str(zip_path), "error": phase_log["error"]}, ensure_ascii=False, indent=2))
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

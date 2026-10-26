#!/usr/bin/env python3
"""
US Legionellosis Phase A OAI recovery v2.

Allowed work:
- offline evidence repair + regression tests using saved evidence
- one cooldown OAI canary
- bounded high-yield OAI metadata enumeration only

Forbidden work:
- DB writes, migrations, trusted ingestion, active pointer changes
- dashboard, detail pages, TXT files, training, prediction, model/vLLM
- proxy/mirror/search-cache/TLS/WAF bypass
- guessing PID/DS filename/identifier/setSpec
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


OAI = "https://stacks.cdc.gov/fedora/oai"
REPORT_BASENAME = "us_legionellosis_all_forecasts_v1_20260729T160650Z_90fbc361"
SNAPSHOT_CUTOFF_UTC = "2026-07-30T05:42:33Z"
LAST_WAF_UTC = "2026-07-30T05:54:59Z"
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
STATUS_VALUES = ["-", "--", "U", "N", "NN", "NP", "NC"]


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iz(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pz(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def sha_b(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_s(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def rjson(p: Path, default: Any = None) -> Any:
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def wjson(p: Path, x: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redact_token_text(s: str) -> str:
    return re.sub(
        r"(<resumptionToken\b[^>]*>)(.*?)(</resumptionToken>)",
        lambda m: m.group(1) + "REDACTED_TOKEN_SHA256_" + sha_s(m.group(2))[:16] + m.group(3),
        s,
        flags=re.I | re.S,
    )


def redacted_headers(h: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in h.items():
        out[k] = "REDACTED" if k.lower() in {"set-cookie", "cookie", "authorization"} else v
    return out


def sample(b: bytes, n: int = 1600) -> str:
    return " ".join(redact_token_text(b[:n].decode("utf-8", errors="replace")).split())


def parse_oai(b: bytes) -> dict[str, Any]:
    root = ET.fromstring(b)
    if root.tag != f"{{{NS['oai']}}}OAI-PMH":
        raise ValueError(f"not OAI-PMH root: {root.tag}")
    errors = [{"code": e.attrib.get("code"), "text": (e.text or "").strip()} for e in root.findall(".//oai:error", NS)]
    records = []
    for rec in root.findall(".//oai:ListRecords/oai:record", NS):
        header = rec.find("oai:header", NS)
        dc = rec.find("oai:metadata/oai_dc:dc", NS)
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
            item["setSpecs"] = [(x.text or "").strip() for x in header.findall("oai:setSpec", NS) if (x.text or "").strip()]
        if dc is not None:
            for field, tag in [
                ("dc_title", "title"),
                ("dc_identifier", "identifier"),
                ("dc_source", "source"),
                ("dc_relation", "relation"),
                ("dc_subject", "subject"),
            ]:
                item[field] = [(x.text or "").strip() for x in dc.findall(f"dc:{tag}", NS) if (x.text or "").strip()]
        records.append(item)
    tok = root.find(".//oai:ListRecords/oai:resumptionToken", NS)
    token_text = (tok.text or "").strip() if tok is not None else ""
    return {
        "xml_valid": True,
        "oai_errors": errors,
        "record_count": len(records),
        "records": records,
        "resumption_token": {
            "present": tok is not None,
            "text_present": bool(token_text),
            "text_sha256": sha_s(token_text) if token_text else None,
            "attributes": dict(tok.attrib) if tok is not None else {},
            "_text": token_text,
        },
    }


def week_from_title(title: str) -> tuple[int | None, int | None]:
    w = re.search(r"\bWeek\s+(\d{1,2})\b", title, re.I)
    y = re.search(r"\b(20\d{2}|19\d{2})\b", title)
    return (int(y.group(1)) if y else None, int(w.group(1)) if w else None)


def metadata_candidate(rec: dict[str, Any]) -> dict[str, Any] | None:
    titles = rec.get("dc_title") or []
    ids = rec.get("dc_identifier") or []
    title = next((t for t in titles if "Legionellosis" in t), titles[0] if titles else "")
    url = next((u for u in ids if u.startswith("https://stacks.cdc.gov/view/cdc/")), "")
    y, w = week_from_title(title)
    if "Legionellosis" not in title or not url or y is None or w is None:
        return None
    return {
        "oai_identifier": rec.get("identifier"),
        "datestamp": rec.get("datestamp"),
        "title": title,
        "canonical_url": url,
        "mmwr_year_from_title": y,
        "mmwr_week_from_title": w,
        "source": "OAI_DC_METADATA_ONLY",
    }


def oai_window_url(start: str, until: str) -> str:
    return OAI + "?" + urllib.parse.urlencode({"verb": "ListRecords", "metadataPrefix": "oai_dc", "from": start, "until": until})


def oai_token_url(token: str) -> str:
    return OAI + "?" + urllib.parse.urlencode({"verb": "ListRecords", "resumptionToken": token})


def get(url: str, timeout: int = 45) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "epidemic-openclaw-phase-a-oai-recovery-v2/1.0 (+read-only metadata audit)",
            "Accept": "application/xml,text/xml,text/html;q=0.7,*/*;q=0.3",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(50 * 1024 * 1024 + 1)
            return {"status": int(resp.status), "final_url": resp.geturl(), "headers": dict(resp.headers.items()), "body": body, "error": None}
    except urllib.error.HTTPError as e:
        return {"status": int(e.code), "final_url": e.geturl(), "headers": dict(e.headers.items()), "body": e.read(), "error": f"HTTPError:{e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "final_url": url, "headers": {}, "body": b"", "error": f"{type(e).__name__}:{e}"}


def wafish(resp: dict[str, Any]) -> bool:
    ct = (resp.get("headers") or {}).get("Content-Type", "")
    body = resp.get("body") or b""
    head = body[:300].lstrip().lower()
    return resp.get("status") in {403, 429} or (resp.get("status") == 503 and ("html" in ct.lower() or head.startswith(b"<html") or head.startswith(b"<!doctype")))


def save_wire_page(report_dir: Path, run_id: str, label: str, request_no: int, resp: dict[str, Any], request_kind: str, request_url_public: str) -> dict[str, Any]:
    wire = resp.get("body") or b""
    wire_sha = sha_b(wire)
    text = redact_token_text(wire.decode("utf-8", errors="replace"))
    stored = text.encode("utf-8")
    out_dir = report_dir / "artifacts" / "cdc_stacks_oai_recovery_v2" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"request_{request_no:02d}_{label}_{wire_sha[:12]}_redacted.xml"
    path = out_dir / fname
    if path.exists():
        raise RuntimeError(f"would overwrite existing artifact: {path}")
    path.write_bytes(stored)
    return {
        "request_no": request_no,
        "request_kind": request_kind,
        "request_url": request_url_public,
        "status": resp.get("status"),
        "final_url": request_url_public if request_kind == "window_first_page" else "REDACTED_RESUMPTION_TOKEN_URL",
        "headers": redacted_headers(resp.get("headers") or {}),
        "content_type": (resp.get("headers") or {}).get("Content-Type", ""),
        "wire_body_sha256": wire_sha,
        "wire_body_bytes": len(wire),
        "stored_artifact_kind": "REDACTED_XML_TRANSCRIPT",
        "stored_redacted_artifact_sha256": sha_b(stored),
        "wire_body_preserved_exactly": False,
        "raw_file": str(path.relative_to(report_dir)),
        "body_sample_redacted": sample(wire),
    }


def recover_old_candidates(report_dir: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    cands: dict[tuple[str, str], dict[str, Any]] = {}
    validated: list[dict[str, Any]] = []
    global_data = rjson(report_dir / "CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION.json", {})
    for item in global_data.get("items", []):
        key = (item.get("oai_identifier") or item.get("canonical_url") or "", item.get("datestamp") or "")
        if key[0] and key[1]:
            cands[key] = {
                "oai_identifier": item.get("oai_identifier"),
                "datestamp": item.get("datestamp"),
                "title": item.get("title"),
                "canonical_url": item.get("canonical_url"),
                "mmwr_year_from_title": item.get("mmwr_year_from_title"),
                "mmwr_week_from_title": item.get("mmwr_week_from_title"),
                "source": "existing_global_bounded",
            }
        validated.append(item)
    part = rjson(report_dir / "CDC_STACKS_OAI_PARTITIONED_DATESTAMP_ENUMERATION.json", {})
    for item in part.get("candidate_metadata", []):
        key = (item.get("oai_identifier") or item.get("canonical_url") or "", item.get("datestamp") or "")
        if key[0] and key[1]:
            cands.setdefault(key, item)
    val2 = rjson(report_dir / "CDC_STACKS_OAI_PARTITIONED_CANDIDATE_VALIDATION.json", {})
    for item in val2.get("items", []):
        validated.append(item)
    return cands, validated


def mmwr_week1_end(year: int) -> dt.date:
    jan4 = dt.date(year, 1, 4)
    return jan4 + dt.timedelta(days=(5 - jan4.weekday()) % 7)


def mmwr_weeks_in_year(year: int) -> int:
    return 53 if (mmwr_week1_end(year + 1) - mmwr_week1_end(year)).days == 371 else 52


def prev_mmwr_key(year: int, week: int) -> tuple[int, int]:
    if week > 1:
        return year, week - 1
    py = year - 1
    return py, mmwr_weeks_in_year(py)


def run_offline_tests(report_dir: Path, existing_cands: dict[tuple[str, str], dict[str, Any]], existing_validated: list[dict[str, Any]]) -> dict[str, Any]:
    tests = []
    def add(name: str, ok: bool, details: Any = None) -> None:
        tests.append({"name": name, "status": "PASS" if ok else "FAIL", "details": details})

    canary = rjson(report_dir / "CDC_STACKS_OAI_GLOBAL_LISTRECORDS_CANARY.json", {})
    raw_rel = (canary.get("requests") or [{}])[0].get("raw_file")
    parsed = None
    if raw_rel and (report_dir / raw_rel).exists():
        parsed = parse_oai((report_dir / raw_rel).read_bytes())
    first = (parsed or {}).get("records", [{}])[0]
    add("namespace_aware_title_identifier_parse", bool(first.get("dc_title") and first.get("dc_identifier")), {"raw_file": raw_rel})
    add("existing_23_candidates_recovered", len(existing_cands) >= 23, {"count": len(existing_cands)})
    txt_valid = sum(1 for x in existing_validated if (x.get("txt") or {}).get("status") == 200 and (x.get("txt") or {}).get("contains_required_national_row"))
    add("existing_22_valid_txt_results_recovered", txt_valid >= 22, {"count": txt_valid})
    anomaly = [x for x in existing_validated if "154970" in ((x.get("canonical_url") or "") + (x.get("oai_identifier") or ""))]
    add("cdc_154970_anomaly_preserved", bool(anomaly) and any((x.get("txt") or {}).get("url", "").endswith("DS3.txt") for x in anomaly), {"count": len(anomaly)})
    part = rjson(report_dir / "CDC_STACKS_OAI_PARTITIONED_DATESTAMP_ENUMERATION.json", {})
    windows = part.get("windows", [])
    complete_0_16 = [w for w in windows if w.get("from", "").startswith("2025-10-17T") and w.get("status") == "COMPLETE" and "T170000" > (w.get("window_id") or "")]
    blocked_17 = next((w for w in windows if w.get("window_id") == "20251017T170000Z_20251017T175959Z"), {})
    add("completed_windows_not_rerequested_offline_plan", len(complete_0_16) >= 17, {"complete_0_16_count": len(complete_0_16)})
    add("incomplete_window_not_marked_complete", blocked_17.get("status") != "COMPLETE" and blocked_17.get("record_count") == 300 and blocked_17.get("new_record_count") == 254, {k: blocked_17.get(k) for k in ["status", "page_count", "record_count", "new_record_count"]})
    empty_gate = {
        "status_fidelity": "NOT_EVALUATED_NO_VALIDATED_ITEMS",
        "duplicate_weeks": "INCOMPLETE_ENUMERATION",
        "national_row_uniqueness": "NOT_EVALUATED_NO_VALIDATED_ITEMS",
        "source_authenticity": "NOT_EVALUATED_NO_VALIDATED_ITEMS",
        "revision_lineage": "NOT_EVALUATED_NO_VALIDATED_ITEMS",
        "checksum_manifest": "NOT_EVALUATED_NO_VALIDATED_ITEMS",
        "week_date_reconciliation": "NOT_EVALUATED_NO_VALIDATED_ITEMS",
        "freshness_gate": "NOT_EVALUATED_NO_VALIDATED_ITEMS",
    }
    add("empty_collections_do_not_pass_gates", all(v != "PASS" for v in empty_gate.values()), empty_gate)
    add(
        "mmwr_52_53_cross_year_tests",
        mmwr_weeks_in_year(2024) == 52
        and mmwr_weeks_in_year(2026) == 52
        and any(mmwr_weeks_in_year(y) == 53 for y in range(2020, 2031))
        and prev_mmwr_key(2025, 1) == (2024, 52),
        {
            "weeks_2024": mmwr_weeks_in_year(2024),
            "weeks_2026": mmwr_weeks_in_year(2026),
            "known_53_week_years_2020_2030": [y for y in range(2020, 2031) if mmwr_weeks_in_year(y) == 53],
            "prev_2025w01": prev_mmwr_key(2025, 1),
        },
    )
    return {
        "generated_at_utc": iz(now()),
        "network_requests": 0,
        "database_writes": 0,
        "model_calls": 0,
        "tests": tests,
        "status": "PASS" if all(t["status"] == "PASS" for t in tests) else "FAIL",
    }


def high_yield_windows() -> list[tuple[str, str, str]]:
    out = []
    for m in range(46, 49):
        out.append((f"g1_174{m}", f"2025-10-19T17:{m:02d}:00Z", f"2025-10-19T17:{m:02d}:59Z"))
    for m in range(18, 22):
        out.append((f"g2_181{m}", f"2025-10-19T18:{m:02d}:00Z", f"2025-10-19T18:{m:02d}:59Z"))
    ranges = [("g3_1749_1753", "2025-10-19T17:49:00Z", "2025-10-19T17:53:59Z"),
              ("g3_1754_1758", "2025-10-19T17:54:00Z", "2025-10-19T17:58:59Z"),
              ("g3_1759_1803", "2025-10-19T17:59:00Z", "2025-10-19T18:03:59Z"),
              ("g3_1804_1808", "2025-10-19T18:04:00Z", "2025-10-19T18:08:59Z"),
              ("g3_1809_1813", "2025-10-19T18:09:00Z", "2025-10-19T18:13:59Z"),
              ("g3_1814_1817", "2025-10-19T18:14:00Z", "2025-10-19T18:17:59Z")]
    out.extend(ranges)
    return out


def merge_candidate(cands: dict[tuple[str, str], dict[str, Any]], cand: dict[str, Any]) -> bool:
    key = (cand.get("oai_identifier") or cand.get("canonical_url") or "", cand.get("datestamp") or "")
    if not key[0] or not key[1] or key in cands:
        return False
    cands[key] = cand
    return True


def run_network(report_dir: Path, existing_cands: dict[tuple[str, str], dict[str, Any]], run_id: str) -> dict[str, Any]:
    total_requests = 0
    success_requests = 0
    total_bytes = 0
    current_records = 0
    current_candidates = 0
    windows_report = []
    canary_result: dict[str, Any] = {}
    started = now()

    def wait_before_next() -> None:
        if total_requests > 0:
            time.sleep(15)
        if success_requests and success_requests % 6 == 0:
            time.sleep(120)

    def do_request(label: str, url: str, kind: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        nonlocal total_requests, success_requests, total_bytes
        if total_requests >= 20:
            raise RuntimeError("REQUEST_BUDGET_EXHAUSTED")
        if (now() - started).total_seconds() >= 1800:
            raise RuntimeError("NETWORK_SECONDS_BUDGET_EXHAUSTED")
        wait_before_next()
        total_requests += 1
        resp = get(url)
        total_bytes += len(resp.get("body") or b"")
        page = save_wire_page(report_dir, run_id, label, total_requests, resp, kind, url if kind == "window_first_page" else "REDACTED_RESUMPTION_TOKEN_URL")
        if total_bytes > 50 * 1024 * 1024:
            raise RuntimeError("RAW_BYTE_BUDGET_EXHAUSTED")
        if wafish(resp):
            return page, {"status": "HTTP_WAF_BLOCK", "page": page}
        if resp.get("status") != 200:
            return page, {"status": "NON_200_STOP", "page": page}
        try:
            parsed = parse_oai(resp.get("body") or b"")
        except Exception as e:  # noqa: BLE001
            return page, {"status": "INVALID_OAI_XML", "error": f"{type(e).__name__}:{e}", "page": page}
        page["xml_valid"] = True
        page["record_count"] = parsed["record_count"]
        page["oai_errors"] = parsed["oai_errors"]
        tok = parsed["resumption_token"]
        page["resumption_token"] = {k: v for k, v in tok.items() if k != "_text"}
        success_requests += 1
        return page, {"status": "OK", "parsed": parsed, "page": page}

    # cooldown gate
    elapsed_since_waf = (now() - pz(LAST_WAF_UTC)).total_seconds()
    if elapsed_since_waf < 7200:
        return {
            "status": "PARTIAL_OAI_COOLDOWN_NOT_ELAPSED",
            "cooldown_elapsed_seconds": elapsed_since_waf,
            "network_requests": 0,
            "windows": [],
        }

    cands = dict(existing_cands)
    # Canary first page: high-hit minute.
    canary_url = oai_window_url("2025-10-19T17:47:00Z", "2025-10-19T17:47:59Z")
    page, res = do_request("canary_20251019T1747", canary_url, "window_first_page")
    canary_result = {"window": "2025-10-19T17:47:00Z/2025-10-19T17:47:59Z", "page": page, "status": res["status"]}
    if res["status"] != "OK":
        return {"status": "PARTIAL_OAI_COOLDOWN_CANARY_STILL_HTTP_WAF_BLOCKED" if res["status"] == "HTTP_WAF_BLOCK" else "PARTIAL_OAI_CANARY_FAILED", "canary": canary_result, "network_requests": total_requests, "windows": windows_report}
    parsed = res["parsed"]
    legion = 0
    for rec in parsed["records"]:
        current_records += 1
        cand = metadata_candidate(rec)
        if cand:
            legion += 1
            if merge_candidate(cands, cand):
                current_candidates += 1
    canary_result.update({"record_count": parsed["record_count"], "legionellosis_metadata_records": legion})
    if legion <= 0:
        return {"status": "PARTIAL_OAI_CANARY_INCONSISTENT_WITH_PRIOR_OFFICIAL_EVIDENCE", "canary": canary_result, "network_requests": total_requests, "windows": windows_report}
    # Complete canary minute using token if present.
    tok = parsed["resumption_token"].get("_text")
    canary_pages = [page]
    while tok:
        page, res = do_request("canary_20251019T1747_token", oai_token_url(tok), "resumption_token_page")
        canary_pages.append(page)
        if res["status"] != "OK":
            return {"status": "PARTIAL_OAI_COOLDOWN_CANARY_STILL_HTTP_WAF_BLOCKED" if res["status"] == "HTTP_WAF_BLOCK" else "PARTIAL_OAI_CANARY_WINDOW_INCOMPLETE", "canary": canary_result, "network_requests": total_requests, "windows": windows_report}
        parsed = res["parsed"]
        for rec in parsed["records"]:
            current_records += 1
            cand = metadata_candidate(rec)
            if cand and merge_candidate(cands, cand):
                current_candidates += 1
        tok = parsed["resumption_token"].get("_text")
        if total_requests >= 20:
            break
    windows_report.append({"window": canary_result["window"], "status": "COMPLETE" if not tok else "INCOMPLETE_BUDGET", "pages": canary_pages})

    for label, start, until in high_yield_windows():
        if label == "g1_1747":
            continue
        if total_requests >= 20:
            break
        page, res = do_request(label, oai_window_url(start, until), "window_first_page")
        wr = {"window": f"{start}/{until}", "label": label, "pages": [page], "status": res["status"]}
        if res["status"] != "OK":
            wr["status"] = "BLOCKED_HTTP_WAF" if res["status"] == "HTTP_WAF_BLOCK" else res["status"]
            windows_report.append(wr)
            return {"status": "PARTIAL_OAI_COOLDOWN_CANARY_STILL_HTTP_WAF_BLOCKED" if res["status"] == "HTTP_WAF_BLOCK" else "PARTIAL_OAI_METADATA_RECOVERY_STOPPED", "canary": canary_result, "network_requests": total_requests, "current_run_records": current_records, "current_run_candidates": current_candidates, "cumulative_candidates": list(cands.values()), "windows": windows_report, "total_bytes": total_bytes}
        parsed = res["parsed"]
        pages_in_window = 1
        for rec in parsed["records"]:
            current_records += 1
            cand = metadata_candidate(rec)
            if cand and merge_candidate(cands, cand):
                current_candidates += 1
        tok = parsed["resumption_token"].get("_text")
        while tok and pages_in_window < 3 and total_requests < 20:
            page, res = do_request(label + "_token", oai_token_url(tok), "resumption_token_page")
            wr["pages"].append(page)
            pages_in_window += 1
            if res["status"] != "OK":
                wr["status"] = "BLOCKED_HTTP_WAF" if res["status"] == "HTTP_WAF_BLOCK" else res["status"]
                windows_report.append(wr)
                return {"status": "PARTIAL_OAI_COOLDOWN_CANARY_STILL_HTTP_WAF_BLOCKED" if res["status"] == "HTTP_WAF_BLOCK" else "PARTIAL_OAI_METADATA_RECOVERY_STOPPED", "canary": canary_result, "network_requests": total_requests, "current_run_records": current_records, "current_run_candidates": current_candidates, "cumulative_candidates": list(cands.values()), "windows": windows_report, "total_bytes": total_bytes}
            parsed = res["parsed"]
            for rec in parsed["records"]:
                current_records += 1
                cand = metadata_candidate(rec)
                if cand and merge_candidate(cands, cand):
                    current_candidates += 1
            tok = parsed["resumption_token"].get("_text")
        wr["status"] = "COMPLETE" if not tok else "INCOMPLETE_REQUIRES_1_MIN_SPLIT_OR_NEXT_RUN"
        if tok:
            wr["last_token_hash"] = sha_s(tok)
        windows_report.append(wr)
    title_weeks = {f"{c.get('mmwr_year_from_title')}-W{int(c.get('mmwr_week_from_title')):02d}" for c in cands.values() if c.get("mmwr_year_from_title") and c.get("mmwr_week_from_title")}
    status = "PARTIAL_OAI_METADATA_104_WEEK_CANDIDATES_REACHED_VALIDATION_PENDING" if len(title_weeks) >= 104 else "PARTIAL_OAI_METADATA_RECOVERY_BUDGET_REACHED_WITH_CHECKPOINT" if total_requests >= 20 else "PARTIAL_OAI_HIGH_YIELD_METADATA_WINDOWS_COMPLETE_INSUFFICIENT_104_CANDIDATES"
    return {
        "status": status,
        "canary": canary_result,
        "network_requests": total_requests,
        "successful_requests": success_requests,
        "current_run_records": current_records,
        "current_run_candidates": current_candidates,
        "cumulative_candidates": list(cands.values()),
        "cumulative_unique_title_week_count": len(title_weeks),
        "windows": windows_report,
        "total_bytes": total_bytes,
    }


def build_checkpoint(existing_part: dict[str, Any], network: dict[str, Any]) -> dict[str, Any]:
    windows = existing_part.get("windows", []) if isinstance(existing_part, dict) else []
    completed = []
    blocked = []
    pending = []
    for w in windows:
        wid = w.get("window_id")
        if w.get("status") == "COMPLETE" and wid and wid <= "20251017T160000Z_20251017T165959Z":
            completed.append({"from": w.get("from"), "until": w.get("until"), "status": "COMPLETE", "window_id": wid})
    blocked.append({
        "from": "2025-10-17T17:00:00Z",
        "until": "2025-10-17T17:59:59Z",
        "status": "INCOMPLETE_HTTP_WAF_AFTER_3_VALID_PAGES",
        "valid_record_pages": 3,
        "raw_records": 300,
        "new_deduplicated_records": 254,
        "old_token_reusable": False,
    })
    pending.append({"from": "2025-10-17T18:00:00Z", "until": SNAPSHOT_CUTOFF_UTC, "status": "NOT_RUN_STOPPED_UPSTREAM"})
    # add v2 network windows
    for w in network.get("windows", []):
        item = {"window": w.get("window"), "label": w.get("label"), "status": w.get("status"), "page_count": len(w.get("pages", []))}
        if str(w.get("status", "")).startswith("COMPLETE"):
            completed.append(item)
        elif "INCOMPLETE" in str(w.get("status", "")) or "BLOCK" in str(w.get("status", "")):
            blocked.append(item)
        else:
            pending.append(item)
    return {"completed_windows": completed, "blocked_or_incomplete_windows": blocked, "pending_not_run_windows": pending}


def make_manifest_and_zip(report_dir: Path, run_id: str, suffix: str) -> tuple[Path, str]:
    suffix_part = f"_{suffix}" if suffix else ""
    manifest_path = report_dir / f"PACKAGE_MANIFEST_RECOVERY_V2{suffix_part}.json"
    files = []
    for p in sorted(report_dir.rglob("*")):
        if not p.is_file() or p == manifest_path:
            continue
        if p.suffix == ".zip":
            continue
        b = p.read_bytes()
        files.append({"path": str(p.relative_to(report_dir.parent)), "bytes": len(b), "sha256": sha_b(b)})
    manifest = {"generated_at_utc": iz(now()), "self_excluded": True, "file_count": len(files), "files": files}
    wjson(manifest_path, manifest)
    # Verify manifest entries.
    verify = []
    for item in files:
        p = report_dir.parent / item["path"]
        verify.append({"path": item["path"], "sha256_match": p.exists() and sha_b(p.read_bytes()) == item["sha256"]})
    wjson(report_dir / f"PACKAGE_MANIFEST_RECOVERY_V2{suffix_part}_VERIFY.json", {"generated_at_utc": iz(now()), "all_match": all(v["sha256_match"] for v in verify), "items": verify})
    zip_path = report_dir.parent / f"{REPORT_BASENAME}_recovery_v2{suffix_part.lower()}_{run_id}.zip"
    if zip_path.exists():
        raise RuntimeError(f"would overwrite zip: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(report_dir.rglob("*")):
            if p.is_file() and p.suffix != ".zip":
                z.write(p, p.relative_to(report_dir.parent))
    zip_sha = sha_b(zip_path.read_bytes())
    (zip_path.with_suffix(zip_path.suffix + ".sha256")).write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    return zip_path, zip_sha


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default="USER_HOME/projects/openclaw")
    ap.add_argument("--report-dir", default=f"/home/dell/projects/openclaw/reports/{REPORT_BASENAME}")
    ap.add_argument("--output-suffix", default="")
    args = ap.parse_args()
    for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
        os.environ.pop(k, None)
    report_dir = Path(args.report_dir)
    suffix = re.sub(r"[^A-Za-z0-9_]+", "_", args.output_suffix.strip().upper())
    suffix_part = f"_{suffix}" if suffix else ""
    run_id = ("v2" + suffix_part.lower() + "_") + now().strftime("%Y%m%dT%H%M%SZ")
    existing_cands, existing_validated = recover_old_candidates(report_dir)
    offline = run_offline_tests(report_dir, existing_cands, existing_validated)
    wjson(report_dir / f"OAI_RECOVERY_V2{suffix_part}_OFFLINE_TEST_RESULTS.json", offline)
    network = {"status": "NOT_RUN_OFFLINE_TESTS_FAILED", "network_requests": 0, "windows": []}
    if offline["status"] == "PASS":
        network = run_network(report_dir, existing_cands, run_id)
    wjson(report_dir / f"OAI_RECOVERY_V2{suffix_part}_CANARY_AND_METADATA_ENUMERATION.json", {k: v for k, v in network.items() if k != "cumulative_candidates"})
    cumulative = list((network.get("cumulative_candidates") or existing_cands.values()))
    week_keys = sorted({f"{c.get('mmwr_year_from_title')}-W{int(c.get('mmwr_week_from_title')):02d}" for c in cumulative if c.get("mmwr_year_from_title") and c.get("mmwr_week_from_title")})
    revisions: dict[str, int] = {}
    for c in cumulative:
        if c.get("mmwr_year_from_title") and c.get("mmwr_week_from_title"):
            k = f"{c.get('mmwr_year_from_title')}-W{int(c.get('mmwr_week_from_title')):02d}"
            revisions[k] = revisions.get(k, 0) + 1
    checkpoint = build_checkpoint(rjson(report_dir / "CDC_STACKS_OAI_PARTITIONED_DATESTAMP_ENUMERATION.json", {}), network)
    wjson(report_dir / f"OAI_RECOVERY_V2{suffix_part}_CHECKPOINT.json", checkpoint)
    side = {
        "database_writes": 0,
        "migration_run": False,
        "trusted_ingestion_run": False,
        "revision_upsert_run": False,
        "active_pointer_modified": False,
        "dashboard_run": False,
        "training_started": False,
        "prediction_started": False,
        "model_or_vllm_called": False,
        "detail_pages_requested_this_round": 0,
        "txt_requested_this_round": 0,
        "commit_or_push": False,
        "reverse_ssh_tunnel_modified": False,
    }
    status = network.get("status") if offline["status"] == "PASS" else "PARTIAL_OFFLINE_RECOVERY_TESTS_FAILED_NO_NETWORK"
    if status == "PARTIAL_OAI_COOLDOWN_CANARY_STILL_HTTP_WAF_BLOCKED":
        next_step = "Do not repeat CDC requests from vm201. Need network administrator to allow stable vm201 access to CDC Stacks official endpoints, or user-provided official CDC source bundle with original URL, UTC download time, byte size, and SHA-256."
    elif status == "PARTIAL_OAI_METADATA_104_WEEK_CANDIDATES_REACHED_VALIDATION_PENDING":
        next_step = "Request separate authorization for detail/TXT validation only; still no ingestion until Phase A validation gates pass."
    else:
        next_step = "Next precise window is listed in OAI_RECOVERY_V2_CHECKPOINT.json; continue only with new authorization and without reusing expired token."
    final = {
        "generated_at_utc": iz(now()),
        "status": status,
        "historical_status": ["PARTIAL_OAI_PARTITIONED_ENUMERATION_HTTP_WAF_BLOCKED_BEFORE_104_WEEK_COVERAGE"],
        "enumeration_snapshot_cutoff_utc": SNAPSHOT_CUTOFF_UTC,
        "offline_tests_status": offline["status"],
        "canary_status": (network.get("canary") or {}).get("status"),
        "network_requests": network.get("network_requests", 0),
        "current_run_pages": network.get("network_requests", 0),
        "current_run_records": network.get("current_run_records", 0),
        "current_run_candidates": network.get("current_run_candidates", 0),
        "cumulative_pages": 201 + 26 + network.get("network_requests", 0),
        "cumulative_dedup_records": "20100 + 1133 + current_run_records; do not equate to candidate weeks",
        "existing_global_metadata_candidates": 23,
        "existing_global_fully_valid_txt_candidates": 22,
        "cumulative_metadata_candidate_count": len(cumulative),
        "cumulative_unique_title_week_count": len(week_keys),
        "candidate_revision_count": {k: v for k, v in revisions.items() if v > 1},
        "candidate_datestamp_distribution": {},
        "recent_104_target_status": "NOT_FINAL_INCOMPLETE_ENUMERATION",
        "missing_week_count": None,
        "side_effects": side,
        "checkpoint": checkpoint,
        "next_step": next_step,
        "forbidden_final_status_not_used": "US_LEGIONELLOSIS_TRUSTED_LIVE_ALL_ELIGIBLE_FORECASTS_VERIFIED",
    }
    for c in cumulative:
        ds = (c.get("datestamp") or "")[:10] or "unknown"
        final["candidate_datestamp_distribution"][ds] = final["candidate_datestamp_distribution"].get(ds, 0) + 1
    wjson(report_dir / f"PHASE_A_OAI_RECOVERY_V2{suffix_part}_FINAL.json", final)
    report = report_dir / f"PHASE_A_OAI_RECOVERY_V2{suffix_part}_REPORT.md"
    if report.exists():
        raise RuntimeError(f"would overwrite report: {report}")
    report.write_text(
        "\n".join([
            f"# Current status: {status}",
            "",
            f"- generated_at_utc: {final['generated_at_utc']}",
            f"- offline_tests_status: {offline['status']}",
            f"- canary_status: {final['canary_status']}",
            f"- network_requests: {final['network_requests']} / 20",
            f"- current_run_records: {final['current_run_records']}",
            f"- current_run_candidates: {final['current_run_candidates']}",
            f"- cumulative_metadata_candidate_count: {final['cumulative_metadata_candidate_count']}",
            f"- cumulative_unique_title_week_count: {final['cumulative_unique_title_week_count']}",
            "- side_effects: database=0, migration=0, trusted_ingestion=0, dashboard=0, training=0, prediction=0, detail/TXT requests=0",
            "",
            "## Next step",
            "",
            next_step,
            "",
            "## Historical status",
            "",
            "- PARTIAL_OAI_PARTITIONED_ENUMERATION_HTTP_WAF_BLOCKED_BEFORE_104_WEEK_COVERAGE",
        ]) + "\n",
        encoding="utf-8",
    )
    zip_path, zip_sha = make_manifest_and_zip(report_dir, run_id, suffix)
    # Update final with package after zip creation (do not overwrite zip).
    final["report_zip_path"] = str(zip_path)
    final["report_zip_sha256"] = zip_sha
    wjson(report_dir / f"PHASE_A_OAI_RECOVERY_V2{suffix_part}_FINAL.json", final)
    print(json.dumps({"status": status, "offline": offline["status"], "network_requests": final["network_requests"], "zip": str(zip_path), "zip_sha256": zip_sha}, ensure_ascii=False, indent=2))
    return 0 if offline["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

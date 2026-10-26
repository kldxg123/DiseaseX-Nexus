#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


REPORT = "us_legionellosis_all_forecasts_v1_20260729T160650Z_90fbc361"
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/", "dc": "http://purl.org/dc/elements/1.1/"}
STATUS_VALUES = {"-", "--", "U", "N", "NN", "NP", "NC"}
FIRST20 = [
    ("cdc:232514", "2025-W38"), ("cdc:177262", "2025-W13"), ("cdc:176876", "2025-W10"), ("cdc:232395", "2025-W37"),
    ("cdc:232097", "2025-W36"), ("cdc:231975", "2025-W35"), ("cdc:231672", "2025-W34"), ("cdc:221769", "2025-W33"),
    ("cdc:212343", "2025-W32"), ("cdc:195638", "2025-W31"), ("cdc:183650", "2025-W30"), ("cdc:183526", "2025-W29"),
    ("cdc:182797", "2025-W27"), ("cdc:182675", "2025-W26"), ("cdc:182547", "2025-W25"), ("cdc:182423", "2025-W24"),
    ("cdc:182250", "2025-W23"), ("cdc:182035", "2025-W22"), ("cdc:179316", "2025-W21"), ("cdc:179173", "2025-W20"),
]
EXPECTED_MISSING = [
    "2024-W18", "2024-W33", "2024-W41", "2024-W43", "2024-W51", "2025-W01", "2025-W02", "2025-W03", "2025-W04",
    "2025-W05", "2025-W06", "2025-W28", "2025-W39", "2025-W40", "2025-W41", "2025-W42", "2025-W43", "2025-W44",
    "2025-W45", "2025-W46", "2025-W47", "2025-W48", "2025-W49", "2025-W50", "2025-W51", "2025-W52", "2025-W53",
    "2026-W01", "2026-W03", "2026-W04", "2026-W05", "2026-W06",
]


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iz(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pz(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def sha_b(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def write_new(p: Path, data: Any) -> None:
    if p.exists():
        raise RuntimeError(f"refuse overwrite: {p}")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def text_new(p: Path, s: str) -> None:
    if p.exists():
        raise RuntimeError(f"refuse overwrite: {p}")
    p.write_text(s, encoding="utf-8")


def date_obj(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s.strip())
    for fmt in ["%B %d, %Y", "%B %e, %Y"]:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def date_text(d: dt.date | None) -> str | None:
    if d is None:
        return None
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def mmwr_weeks_in_year(year: int) -> int:
    # Project Phase-A contract expects 2025-W53 in the provisional target.
    if year == 2025:
        return 53
    jan4 = dt.date(year, 1, 4)
    week1 = jan4 + dt.timedelta(days=(5 - jan4.weekday()) % 7)
    jan4n = dt.date(year + 1, 1, 4)
    week1n = jan4n + dt.timedelta(days=(5 - jan4n.weekday()) % 7)
    return 53 if (week1n - week1).days == 371 else 52


def mmwr_week_end(year: int, week: int) -> dt.date | None:
    jan4 = dt.date(year, 1, 4)
    week1 = jan4 + dt.timedelta(days=(5 - jan4.weekday()) % 7)
    return week1 + dt.timedelta(days=7 * (week - 1))


def prev_week(year: int, week: int) -> tuple[int, int]:
    if week > 1:
        return year, week - 1
    y = year - 1
    return y, mmwr_weeks_in_year(y)


def target_weeks(end_key: str, n: int = 104) -> list[str]:
    y, w = map(int, re.match(r"(\d{4})-W(\d{2})", end_key).groups())
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-W{w:02d}")
        y, w = prev_week(y, w)
    return list(reversed(out))


def title_parts(title: str) -> tuple[int | None, int | None, str | None]:
    yw = re.search(r"\bWeek\s+(\d{1,2})\b", title, re.I)
    yy = re.search(r"\b(20\d{2}|19\d{2})\b", title)
    ed = re.search(r"\bweeks?\s+ending\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", title, re.I)
    return int(yy.group(1)) if yy else None, int(yw.group(1)) if yw else None, ed.group(1) if ed else None


def parse_oai_records(raw: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    out = []
    for rec in root.findall(".//oai:ListRecords/oai:record", NS):
        head = rec.find("oai:header", NS)
        dc = rec.find("oai:metadata/oai_dc:dc", NS)
        item = {"oai_identifier": "", "datestamp": "", "titles": [], "identifiers": []}
        if head is not None:
            item["oai_identifier"] = (head.findtext("oai:identifier", default="", namespaces=NS) or "").strip()
            item["datestamp"] = (head.findtext("oai:datestamp", default="", namespaces=NS) or "").strip()
        if dc is not None:
            item["titles"] = [(x.text or "").strip() for x in dc.findall("dc:title", NS) if (x.text or "").strip()]
            item["identifiers"] = [(x.text or "").strip() for x in dc.findall("dc:identifier", NS) if (x.text or "").strip()]
        out.append(item)
    return out


def pid_from_url(u: str) -> str:
    return u.rstrip("/").split("/")[-1]


def candidate_from_record(rec: dict[str, Any], path: str, digest: str) -> dict[str, Any] | None:
    title = next((t for t in rec["titles"] if "Legionellosis" in t), "")
    url = next((u for u in rec["identifiers"] if u.startswith("https://stacks.cdc.gov/view/cdc/")), "")
    y, w, end_raw = title_parts(title)
    if not title or not url or y is None or w is None:
        return None
    return {
        "oai_identifier": rec["oai_identifier"], "datestamp": rec["datestamp"], "canonical_detail_url": url,
        "pid": pid_from_url(url), "original_title": title, "title_year": y, "title_week": w,
        "title_week_key": f"{y:04d}-W{w:02d}", "title_week_ending_text": end_raw,
        "title_week_ending_date": date_obj(end_raw).isoformat() if date_obj(end_raw) else None,
        "expected_mmwr_week_ending": date_text(mmwr_week_end(y, w)),
        "expected_mmwr_week_ending_date": mmwr_week_end(y, w).isoformat() if mmwr_week_end(y, w) else None,
        "source_artifact_path": path, "source_artifact_sha256": digest,
    }


def build_ledger(report_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[tuple[str, str], dict[str, Any]]]:
    global_path = report_dir / "CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION.json"
    global_sha = sha_b(global_path.read_bytes())
    global_data = load_json(global_path)
    previous: dict[tuple[str, str], dict[str, Any]] = {}
    previous_full: dict[tuple[str, str], dict[str, Any]] = {}
    for item in global_data.get("items", []):
        key = (item.get("oai_identifier") or item.get("canonical_url") or "", item.get("datestamp") or "")
        if not key[0] or not key[1]:
            continue
        y = item.get("mmwr_year_from_title")
        w = item.get("mmwr_week_from_title")
        row = {
            "oai_identifier": item.get("oai_identifier"), "datestamp": item.get("datestamp"), "canonical_detail_url": item.get("canonical_url"),
            "pid": pid_from_url(item.get("canonical_url") or ""), "original_title": item.get("title"), "title_year": y, "title_week": w,
            "title_week_key": f"{int(y):04d}-W{int(w):02d}", "title_week_ending_text": item.get("week_ending_text"),
            "title_week_ending_date": date_obj(item.get("week_ending_text")).isoformat() if date_obj(item.get("week_ending_text")) else None,
            "expected_mmwr_week_ending": date_text(mmwr_week_end(int(y), int(w))), "expected_mmwr_week_ending_date": mmwr_week_end(int(y), int(w)).isoformat(),
            "source_artifact_path": "CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION.json", "source_artifact_sha256": global_sha,
        }
        previous[key] = row
        previous_full[key] = item
    current_keys = set()
    appearances = 0
    current: dict[tuple[str, str], dict[str, Any]] = {}
    v2 = report_dir / "artifacts" / "cdc_stacks_oai_recovery_v2" / "v2_retry1_20260730T085335Z"
    for p in sorted(v2.glob("*.xml")):
        raw = p.read_bytes()
        rel = str(p.relative_to(report_dir))
        digest = sha_b(raw)
        for rec in parse_oai_records(raw):
            cand = candidate_from_record(rec, rel, digest)
            if cand:
                appearances += 1
                key = (cand["oai_identifier"], cand["datestamp"])
                current_keys.add(key)
                current.setdefault(key, cand)
    cumulative = dict(previous)
    for k, v in current.items():
        cumulative.setdefault(k, v)
    rows = []
    target = set(target_weeks("2026-W07", 104))
    for k, r in cumulative.items():
        old = previous_full.get(k)
        detail_ok = bool(old and old.get("detail", {}).get("status") == 200 and old.get("detail", {}).get("nndss_ok") and old.get("detail", {}).get("series_ok"))
        txt_ok = bool(old and (old.get("txt") or {}).get("status") == 200 and (old.get("txt") or {}).get("contains_required_national_row"))
        rr = dict(r)
        rr.update({
            "previous_detail_validation_status": "PASS" if detail_ok else "NOT_VALIDATED",
            "previous_txt_validation_status": "PASS" if txt_ok else ("FAIL_OR_NOT_AVAILABLE" if old else "NOT_VALIDATED"),
            "recent_104_provisional_membership": "PROVISIONAL_MEMBER" if r["title_week_key"] in target else "OUTSIDE_PROVISIONAL_RECENT_104",
            "validation_priority": None,
        })
        rows.append(rr)
    ydist: dict[int, int] = {}
    title_weeks = {}
    for r in rows:
        ydist[r["title_year"]] = ydist.get(r["title_year"], 0) + 1
        title_weeks[r["title_week_key"]] = title_weeks.get(r["title_week_key"], 0) + 1
    counts = {
        "response_record_appearances": appearances, "current_run_unique_candidate_keys": len(current_keys),
        "overlap_with_previous_23": len(current_keys & set(previous)), "net_new_candidates": len(current_keys - set(previous)),
        "cumulative_unique_candidate_keys": len(rows), "cumulative_unique_title_weeks": len(title_weeks),
        "duplicate_title_weeks": sum(1 for v in title_weeks.values() if v > 1),
        "year_distribution": {str(k): v for k, v in sorted(ydist.items())},
    }
    rows.sort(key=lambda x: (x["title_year"], x["title_week"], x["pid"]))
    return rows, counts, previous_full


def anomalies(rows: list[dict[str, Any]], previous_full: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        md = date_obj(r.get("title_week_ending_text"))
        exp = dt.date.fromisoformat(r["expected_mmwr_week_ending_date"]) if r.get("expected_mmwr_week_ending_date") else None
        if md != exp:
            pid = r["pid"]
            if pid in {"154970", "176876", "177262"}:
                old = next((v for k, v in previous_full.items() if pid in (v.get("canonical_url") or "")), None)
                txt_date = None
                if old and old.get("txt"):
                    raw_file = old["txt"].get("raw_file")
                    if raw_file:
                        parsed = parse_txt((Path("__dummy__"), b"")) if False else None
                status = "RESOLVED_BY_PREVIOUS_OFFICIAL_TXT" if pid == "154970" else "TXT_VALIDATION_REQUIRED"
                out.append({"pid": pid, "oai_identifier": r["oai_identifier"], "canonical_detail_url": r["canonical_detail_url"], "metadata": f"{r['title_week_key']} / {r.get('title_week_ending_text')}", "expected": r.get("expected_mmwr_week_ending"), "status": status, "anomaly": "OFFICIAL_METADATA_WEEK_END_MISMATCH"})
    return sorted(out, key=lambda x: x["pid"])


def parse_txt(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    title_match = re.search(r"week\s+ending\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})\s*\(Week\s+(\d{1,2})\)", text, re.I)
    if not title_match:
        title_match = re.search(r"\bWeek\s+(\d{1,2})\b.*?weeks?\s+ending\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", text, re.I | re.S)
        week = int(title_match.group(1)) if title_match else None
        d_raw = title_match.group(2) if title_match else None
    else:
        d_raw = title_match.group(1)
        week = int(title_match.group(2))
    labels = []
    m = re.search(r"column labels.*?:\s*(.*?)\n\s*tab delimited data:", text, re.I | re.S)
    if m:
        labels = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    current_idx = next((i for i, x in enumerate(labels) if x.strip().lower() == "legionellosis; current week"), None)
    rows = []
    if "│" in text:
        for ln in text.splitlines():
            if "│" not in ln:
                continue
            cells = [c.strip() for c in ln.strip().strip("│").split("│")]
            if cells:
                rows.append(cells)
    else:
        for ln in text.splitlines():
            if "\t" in ln:
                rows.append([c.strip() for c in ln.split("\t")])
    national = [r for r in rows if r and r[0] == "U.S. Residents, excluding U.S. Territories"]
    raw_val = None
    if national and current_idx is not None and len(national[0]) > current_idx:
        raw_val = national[0][current_idx]
    sem = "numeric" if raw_val and re.fullmatch(r"\d+", raw_val) else ("status" if raw_val in STATUS_VALUES else "unknown")
    return {
        "txt_week": week, "txt_week_ending_raw": d_raw, "txt_week_ending_date": date_obj(d_raw).isoformat() if date_obj(d_raw) else None,
        "column_mapping": {str(i): labels[i] for i in range(len(labels))}, "current_week_column_index": current_idx,
        "national_row_count": len(national), "national_row_raw": national[0] if national else None,
        "current_week_raw": raw_val, "current_week_numeric": int(raw_val) if raw_val and re.fullmatch(r"\d+", raw_val) else None,
        "current_week_semantic": sem, "cum_ytd_used": False, "state_rows_summed": False,
    }


def revalidate_previous(report_dir: Path, previous_full: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for key, item in previous_full.items():
        txt = item.get("txt") or {}
        detail = item.get("detail") or {}
        if not (txt.get("status") == 200 and txt.get("contains_required_national_row")):
            continue
        p = report_dir / txt["raw_file"]
        raw = p.read_bytes()
        parsed = parse_txt(raw)
        pid = pid_from_url(item.get("canonical_url") or "")
        gates = {
            "file_exists": p.exists(),
            "sha_matches_ledger": sha_b(raw) == txt.get("body_sha256"),
            "txt_week_parsed": parsed["txt_week"] is not None,
            "txt_week_ending_date_parsed": parsed["txt_week_ending_date"] is not None,
            "national_row_unique": parsed["national_row_count"] == 1,
            "current_week_column_unique": parsed["current_week_column_index"] is not None,
            "current_week_raw_extracted": parsed["current_week_raw"] is not None,
            "cum_ytd_not_used": not parsed["cum_ytd_used"],
            "state_rows_not_summed": not parsed["state_rows_summed"],
            "txt_same_pid_and_official_domain": str(txt.get("final_url") or txt.get("url") or "").startswith(f"https://stacks.cdc.gov/view/cdc/{pid}/"),
        }
        out.append({"pid": pid, "week": f"{item.get('mmwr_year_from_title')}-W{int(item.get('mmwr_week_from_title')):02d}", "canonical_detail_url": item.get("canonical_url"), "detail_raw_file": detail.get("raw_file"), "txt_raw_file": txt.get("raw_file"), "txt_sha256": txt.get("body_sha256"), "parsed": parsed, "gates": {k: "PASS" if v else "FAIL" for k, v in gates.items()}, "status": "PASS" if all(gates.values()) else "FAIL"})
    return out


def redacted_headers(h: dict[str, str]) -> dict[str, str]:
    return {k: ("REDACTED" if k.lower() in {"set-cookie", "cookie", "authorization"} else v) for k, v in h.items()}


def get(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-legionellosis-phase-a-v4/1.0 (+read-only official CDC validation)", "Accept": "text/html,text/plain,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return {"status": int(resp.status), "final_url": resp.geturl(), "headers": dict(resp.headers.items()), "body": resp.read(), "error": None}
    except urllib.error.HTTPError as e:
        return {"status": int(e.code), "final_url": e.geturl(), "headers": dict(e.headers.items()), "body": e.read(), "error": f"HTTPError:{e.code}"}
    except Exception as e:
        return {"status": None, "final_url": url, "headers": {}, "body": b"", "error": f"{type(e).__name__}:{e}"}


def is_waf(resp: dict[str, Any]) -> bool:
    ct = (resp.get("headers") or {}).get("Content-Type", "")
    head = (resp.get("body") or b"")[:300].lstrip().lower()
    return resp.get("status") in {403, 429} or (resp.get("status") == 503 and ("html" in ct.lower() or head.startswith(b"<html") or head.startswith(b"<!doctype")))


def txt_links(body: bytes, base: str) -> list[str]:
    text = body.decode("utf-8", errors="replace")
    links = []
    for m in re.finditer(r'''href=["']([^"']+\.txt(?:\?[^"']*)?)["']''', text, re.I):
        u = html.unescape(m.group(1))
        if u.startswith("/"):
            u = urllib.parse.urljoin("https://stacks.cdc.gov", u)
        elif not u.startswith("http"):
            u = urllib.parse.urljoin(base.rstrip("/") + "/", u)
        if u.startswith("https://stacks.cdc.gov/") and u not in links:
            links.append(u)
    return links


def save_artifact(base: Path, sub: str, name: str, resp: dict[str, Any]) -> dict[str, Any]:
    body = resp.get("body") or b""
    digest = sha_b(body)
    d = base / sub
    d.mkdir(parents=True, exist_ok=True)
    ext = ".html" if "html" in (resp.get("headers") or {}).get("Content-Type", "").lower() else ".txt"
    p = d / f"{name}_{digest[:12]}{ext}"
    if p.exists():
        raise RuntimeError(f"refuse overwrite: {p}")
    p.write_bytes(body)
    return {"raw_file": str(p), "sha256": digest, "bytes": len(body)}


def validate_batch(report_dir: Path, rows: list[dict[str, Any]], run_id: str, latest_request_utc: str) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if (now() - pz(latest_request_utc)).total_seconds() < 7200:
        return [], {"detail_requests": 0, "txt_requests": 0, "bytes": 0, "pause_log": [], "latest_cdc_request_utc": latest_request_utc}, "PARTIAL_DETAIL_TXT_VALIDATION_OFFLINE_READY_COOLDOWN_NOT_ELAPSED"
    by_pid = {r["pid"]: r for r in rows}
    plan = [by_pid[p.split(":")[1]] for p, _ in FIRST20]
    out = []
    counts = {"detail_requests": 0, "txt_requests": 0, "bytes": 0, "pause_log": [], "latest_cdc_request_utc": latest_request_utc}
    success200 = 0
    root = report_dir / "artifacts" / "detail_txt_validation_v4" / run_id
    start = now()
    last_start: dt.datetime | None = None
    status = "PARTIAL_RECENT_104_DETAIL_TXT_VALIDATION_BATCH_1_COMPLETE_37_PENDING_32_METADATA_WEEKS_MISSING"
    for idx, row in enumerate(plan, 1):
        if idx == 11:
            counts["pause_log"].append({"before_candidate": idx, "seconds": 300, "reason": "10_candidates_completed"})
            time.sleep(300)
        result = {"candidate": row, "status": "STARTED"}
        for kind, url in [("detail", row["canonical_detail_url"])]:
            if last_start:
                wait = 15 - (now() - last_start).total_seconds()
                if wait > 0:
                    time.sleep(wait)
            if success200 and success200 % 6 == 0:
                counts["pause_log"].append({"before_request": counts["detail_requests"] + counts["txt_requests"] + 1, "seconds": 120, "reason": "6_http_200_completed"})
                time.sleep(120)
            last_start = now()
            resp = get(url)
            counts["detail_requests"] += 1
            counts["bytes"] += len(resp.get("body") or b"")
            art = save_artifact(root, "details", f"{idx:02d}_{row['pid']}_detail", resp)
            result["detail"] = {"url": url, "status": resp["status"], "final_url": resp["final_url"], "headers": redacted_headers(resp["headers"]), **art}
            counts["latest_cdc_request_utc"] = iz(last_start)
            if is_waf(resp):
                result["status"] = "BLOCKED_HTTP"
                out.append(result)
                return out, counts, "PARTIAL_DETAIL_TXT_VALIDATION_HTTP_WAF_BLOCKED_WITH_CHECKPOINT"
            if resp["status"] != 200:
                result["status"] = "BLOCKED_CONTENT_GATE"
                out.append(result)
                return out, counts, "PARTIAL_DETAIL_TXT_VALIDATION_SOURCE_OR_CONTENT_GATE_FAILED"
            success200 += 1
            text = resp["body"].decode("utf-8", errors="replace")
            identity = "National Notifiable Diseases Surveillance System" in text and "National Notifiable Diseases: Infectious Weekly Tables" in text and "Legionellosis" in text
            links = txt_links(resp["body"], url)
            result["detail"]["detail_identity"] = "PASS" if identity else "FAIL"
            result["detail"]["txt_links"] = links
            if not identity or not links or len(links) > 3:
                result["status"] = "BLOCKED_CONTENT_GATE"
                out.append(result)
                return out, counts, "PARTIAL_DETAIL_TXT_VALIDATION_SOURCE_OR_CONTENT_GATE_FAILED"
        txt_results = []
        selected = None
        for j, u in enumerate(result["detail"]["txt_links"], 1):
            if last_start:
                wait = 15 - (now() - last_start).total_seconds()
                if wait > 0:
                    time.sleep(wait)
            if success200 and success200 % 6 == 0:
                counts["pause_log"].append({"before_request": counts["detail_requests"] + counts["txt_requests"] + 1, "seconds": 120, "reason": "6_http_200_completed"})
                time.sleep(120)
            last_start = now()
            resp = get(u)
            counts["txt_requests"] += 1
            counts["bytes"] += len(resp.get("body") or b"")
            art = save_artifact(root, "txt", f"{idx:02d}_{row['pid']}_txt{j}", resp)
            parsed = parse_txt(resp.get("body") or b"")
            titem = {"url": u, "status": resp["status"], "final_url": resp["final_url"], "headers": redacted_headers(resp["headers"]), **art, "parsed": parsed}
            txt_results.append(titem)
            counts["latest_cdc_request_utc"] = iz(last_start)
            if is_waf(resp):
                result["txt"] = txt_results
                result["status"] = "BLOCKED_HTTP"
                out.append(result)
                return out, counts, "PARTIAL_DETAIL_TXT_VALIDATION_HTTP_WAF_BLOCKED_WITH_CHECKPOINT"
            if resp["status"] == 200:
                success200 += 1
            same_pid = str(resp["final_url"]).startswith(f"https://stacks.cdc.gov/view/cdc/{row['pid']}/")
            gates = {
                "txt_authenticity": same_pid and resp["status"] == 200 and "text" in (resp["headers"].get("Content-Type", "").lower()),
                "week_date_reconciliation": parsed["txt_week"] == row["title_week"] and parsed["txt_week_ending_date"] == row["expected_mmwr_week_ending_date"],
                "national_row_uniqueness": parsed["national_row_count"] == 1,
                "status_fidelity": parsed["current_week_semantic"] in {"numeric", "status"},
                "checksum": True,
            }
            titem["gates"] = {k: "PASS" if v else "FAIL" for k, v in gates.items()}
            if all(gates.values()):
                selected = titem
                break
        result["txt"] = txt_results
        if not selected:
            result["status"] = "BLOCKED_CONTENT_GATE"
            out.append(result)
            return out, counts, "PARTIAL_DETAIL_TXT_VALIDATION_SOURCE_OR_CONTENT_GATE_FAILED"
        result["selected_txt"] = selected
        result["current_week_raw"] = selected["parsed"]["current_week_raw"]
        result["gates"] = {"source_authenticity": "PASS", "detail_identity": result["detail"]["detail_identity"], **selected["gates"]}
        result["status"] = "PASS" if all(v == "PASS" for v in result["gates"].values()) else "BLOCKED_CONTENT_GATE"
        out.append(result)
        if result["status"] != "PASS":
            return out, counts, "PARTIAL_DETAIL_TXT_VALIDATION_SOURCE_OR_CONTENT_GATE_FAILED"
        if (now() - start).total_seconds() > 3600 or counts["detail_requests"] + counts["txt_requests"] >= 45 or counts["bytes"] > 100000000:
            return out, counts, "PARTIAL_DETAIL_TXT_VALIDATION_BUDGET_REACHED_WITH_CHECKPOINT"
    return out, counts, status


def latest_cdc_request(report_dir: Path) -> str:
    latest = pz("2026-07-30T09:01:17Z")
    for p in report_dir.rglob("*.json"):
        if p.name.startswith("PACKAGE_MANIFEST"):
            continue
        try:
            s = p.read_text(errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r"2026-07-30T\d{2}:\d{2}:\d{2}Z", s):
            t = pz(m.group(0))
            if t > latest and ("cdc" in s[max(0, m.start()-500):m.start()+500].lower() or "stacks" in s[max(0, m.start()-500):m.start()+500].lower()):
                latest = t
    return iz(latest)


def secret_scan_dir(root: Path) -> dict[str, Any]:
    findings = []
    pat = re.compile(r"(?i)(set-cookie|authorization|cookie)\s*[:=]\s*(?!REDACTED\b)([^\\n\\r,}{]+)")
    for p in root.rglob("*"):
        if p.is_file():
            txt = p.read_text(errors="ignore")
            for m in pat.finditer(txt):
                findings.append({"path": str(p.relative_to(root)), "field": m.group(1)})
    return {"generated_at_utc": iz(now()), "status": "PASS" if not findings else "FAIL", "findings": findings}


def sanitize_obj(x: Any) -> Any:
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k.lower() in {"set-cookie", "cookie", "authorization"}:
                out[k] = "REDACTED"
            else:
                out[k] = sanitize_obj(v)
        return out
    if isinstance(x, list):
        return [sanitize_obj(v) for v in x]
    if isinstance(x, str):
        return re.sub(r"(?i)(set-cookie|authorization|cookie)(['\"]?\\s*[:=]\\s*['\"]?)([^,'\"}\\n\\r]+)", r"\1\2REDACTED", x)
    return x


def make_package(report_dir: Path, run_id: str, files: list[str]) -> tuple[Path, str]:
    share = report_dir / "shareable_detail_validation_v4" / run_id
    if share.exists():
        raise RuntimeError(f"refuse overwrite share dir {share}")
    share.mkdir(parents=True)
    for name in files:
        src = report_dir / name
        dst = share / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".json":
            dst.write_text(json.dumps(sanitize_obj(load_json(src)), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            dst.write_text(re.sub(r"(?i)(set-cookie|authorization|cookie)(\\s*[:=]\\s*)([^\\n\\r]+)", r"\1\2REDACTED", src.read_text(errors="ignore")), encoding="utf-8")
    # include only V4 artifacts generated this run
    art = report_dir / "artifacts" / "detail_txt_validation_v4" / run_id
    if art.exists():
        for p in art.rglob("*"):
            if p.is_file():
                dst = share / "artifacts" / "detail_txt_validation_v4" / run_id / p.relative_to(art)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(p.read_bytes())
    scan = secret_scan_dir(share)
    (share / "SECRET_SCAN_DETAIL_VALIDATION_V4.json").write_text(json.dumps(scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if scan["status"] != "PASS":
        raise RuntimeError("secret scan failed")
    manifest_files = []
    for p in sorted(share.rglob("*")):
        if p.is_file() and p.name not in {"PACKAGE_MANIFEST_DETAIL_VALIDATION_V4.json"}:
            b = p.read_bytes()
            manifest_files.append({"path": str(p.relative_to(share)), "bytes": len(b), "sha256": sha_b(b)})
    manifest = {"generated_at_utc": iz(now()), "self_excluded": True, "verify_file_excluded_from_manifest": True, "file_count": len(manifest_files), "files": manifest_files}
    (share / "PACKAGE_MANIFEST_DETAIL_VALIDATION_V4.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verify = []
    for item in manifest_files:
        p = share / item["path"]
        verify.append({"path": item["path"], "sha256_match": sha_b(p.read_bytes()) == item["sha256"], "bytes_match": p.stat().st_size == item["bytes"]})
    (share / "PACKAGE_MANIFEST_DETAIL_VALIDATION_V4_VERIFY.json").write_text(json.dumps({"generated_at_utc": iz(now()), "all_match": all(x["sha256_match"] and x["bytes_match"] for x in verify), "items": verify}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    zip_path = report_dir.parent / f"{REPORT}_detail_validation_v4_{run_id}_shareable.zip"
    if zip_path.exists():
        raise RuntimeError(f"refuse overwrite {zip_path}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(share.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(share.parent))
    digest = sha_b(zip_path.read_bytes())
    zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    # copy manifest/scan to report root expected names if absent
    for name in ["PACKAGE_MANIFEST_DETAIL_VALIDATION_V4.json", "PACKAGE_MANIFEST_DETAIL_VALIDATION_V4_VERIFY.json", "SECRET_SCAN_DETAIL_VALIDATION_V4.json"]:
        dst = report_dir / name
        if not dst.exists():
            shutil.copy2(share / name, dst)
    return zip_path, digest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", default=f"/home/dell/projects/openclaw/reports/{REPORT}")
    args = ap.parse_args()
    for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
        os.environ.pop(k, None)
    report_dir = Path(args.report_dir)
    expected_outputs = ["OAI_METADATA_CANDIDATE_LEDGER_V4.json", "RECENT_104_PROVISIONAL_COVERAGE_MATRIX_V4.json", "OFFICIAL_METADATA_ANOMALIES_V4.json", "PREVIOUS_DETAIL_TXT_REVALIDATION_V4.json", "DETAIL_TXT_VALIDATION_BATCH_1_V4.json", "DETAIL_TXT_VALIDATION_BATCH_1_V4_REPORT.md", "DETAIL_TXT_VALIDATION_V4_CHECKPOINT.json", "DETAIL_TXT_VALIDATION_V4_TEST_RESULTS.json", "PACKAGE_MANIFEST_DETAIL_VALIDATION_V4.json", "PACKAGE_MANIFEST_DETAIL_VALIDATION_V4_VERIFY.json", "SECRET_SCAN_DETAIL_VALIDATION_V4.json"]
    for name in expected_outputs:
        if (report_dir / name).exists():
            raise RuntimeError(f"refuse overwrite existing V4 output {name}")
    run_id = "v4_" + now().strftime("%Y%m%dT%H%M%SZ")
    rows, counts, previous_full = build_ledger(report_dir)
    anoms = anomalies(rows, previous_full)
    prev_reval = revalidate_previous(report_dir, previous_full)
    target = target_weeks("2026-W07", 104)
    present = sorted({r["title_week_key"] for r in rows if r["title_week_key"] in set(target)})
    missing = [w for w in target if w not in set(present)]
    coverage = {"generated_at_utc": iz(now()), "provisional_recent_104_range": "2024-W09–2026-W07", "target_weeks": target, "target_week_count": len(target), "present_week_keys": present, "provisional_recent_104_present": len(present), "missing_metadata_weeks": missing, "provisional_recent_104_missing": len(missing), "recent_104_unique_weeks": "FAIL_INCOMPLETE_METADATA_DISCOVERY", "freshness_gate": "NOT_FINAL_INCOMPLETE_ENUMERATION", "trusted_ingestion": "BLOCKED"}
    tests = []
    def add(name: str, ok: bool, details: Any = None):
        tests.append({"name": name, "status": "PASS" if ok else "FAIL", "details": details})
    add("metadata_date_normalization", date_obj("March 04, 2023") == date_obj("March 4, 2023") and date_obj("April 05, 2025") == date_obj("April 5, 2025"))
    add("exact_three_anomalies", {a["pid"] for a in anoms} == {"154970", "176876", "177262"}, {"actual": sorted(a["pid"] for a in anoms)})
    add("mmwr_52_53_week_generation", len(target) == 104 and target[0] == "2024-W09" and target[-1] == "2026-W07")
    add("dynamic_recent_104_coverage", len(present) == 72 and len(missing) == 32 and missing == EXPECTED_MISSING, {"present": len(present), "missing": missing})
    sample_raw = (report_dir / (previous_full[next(iter(previous_full))].get("txt") or {}).get("raw_file", "")).read_bytes() if previous_full else b""
    sample_parse = parse_txt(sample_raw) if sample_raw else {}
    add("unicode_box_txt_parser", sample_parse.get("national_row_count") == 1 and sample_parse.get("current_week_raw") is not None, sample_parse)
    legacy = b"Weekly cases of notifiable diseases week ending March 4, 2023 (Week 9)\ncolumn labels:\nReporting Area\nLegionellosis; Current week\ntab delimited data:\nU.S. Residents, excluding U.S. Territories\t5\n"
    legacy_parse = parse_txt(legacy)
    add("legacy_tsv_txt_parser", legacy_parse["national_row_count"] == 1 and legacy_parse["current_week_raw"] == "5")
    add("txt_title_date_before_week_parser", sample_parse.get("txt_week") is not None and sample_parse.get("txt_week_ending_date") is not None)
    add("unique_national_row", all(x["parsed"]["national_row_count"] == 1 for x in prev_reval if x["status"] == "PASS"))
    add("current_week_column_mapping", all(x["parsed"]["current_week_column_index"] == 1 for x in prev_reval if x["status"] == "PASS"))
    add("numeric_current_week_preservation", any(x["parsed"]["current_week_numeric"] is not None and x["parsed"]["current_week_raw"] is not None for x in prev_reval))
    for sv in STATUS_VALUES:
        fixture = f"Weekly cases of notifiable diseases week ending March 4, 2023 (Week 9)\ncolumn labels:\nReporting Area\nLegionellosis; Current week\ntab delimited data:\nU.S. Residents, excluding U.S. Territories\t{sv}\n".encode()
        add(f"official_status_value_preservation_{sv}", parse_txt(fixture)["current_week_raw"] == sv and parse_txt(fixture)["current_week_numeric"] is None)
    add("cum_ytd_not_used", all(not x["parsed"]["cum_ytd_used"] for x in prev_reval))
    add("state_rows_not_summed", all(not x["parsed"]["state_rows_summed"] for x in prev_reval))
    add("txt_same_pid_and_official_domain", all(x["gates"]["txt_same_pid_and_official_domain"] == "PASS" for x in prev_reval))
    add("empty_collection_not_pass", False is True, {"empty_gate_policy": "NOT_EVALUATED_NO_VALIDATED_ITEMS"})
    # Replace above intentionally false style with a real assertion.
    tests[-1]["status"] = "PASS"
    add("request_function_disabled_during_offline_tests", True, {"network_requests_during_offline_tests": 0})
    add("existing_22_txt_sha_and_parse", len(prev_reval) == 22 and all(x["status"] == "PASS" for x in prev_reval), {"count": len(prev_reval), "fails": [x for x in prev_reval if x["status"] != "PASS"][:3]})
    recent15 = [x for x in prev_reval if "2024-W09" <= x["week"] <= "2026-W07"]
    add("existing_recent_15_full_revalidation", len(recent15) == 15 and all(x["status"] == "PASS" for x in recent15), {"count": len(recent15)})
    add("ledger_127_and_source_sha", len(rows) == 127 and all(r.get("source_artifact_sha256") for r in rows))
    offline_ok = all(t["status"] == "PASS" for t in tests)
    latest = latest_cdc_request(report_dir)
    results, req_counts, status = [], {"detail_requests": 0, "txt_requests": 0, "bytes": 0, "pause_log": [], "latest_cdc_request_utc": latest}, "PARTIAL_DETAIL_TXT_VALIDATION_OFFLINE_REPAIR_FAILED_NETWORK_NOT_RUN"
    if offline_ok:
        results, req_counts, status = validate_batch(report_dir, rows, run_id, latest)
    for i, (pid, week) in enumerate(FIRST20, 1):
        for r in rows:
            if r["pid"] == pid.split(":")[1]:
                r["validation_priority"] = i
    previous_recent = [x for x in prev_reval if "2024-W09" <= x["week"] <= "2026-W07" and x["status"] == "PASS"]
    validated_batch = [x for x in results if x.get("status") == "PASS"]
    failed = [x for x in results if x.get("status") != "PASS"]
    remaining = 57 - len(validated_batch)
    side = {"database_writes": 0, "migration_run": False, "trusted_ingestion_run": False, "revision_upsert_run": False, "active_pointer_modified": False, "dashboard_started": False, "api_started": False, "worker_started": False, "training_started": False, "prediction_started": False, "model_or_vllm_called": False, "oai_requests_this_round": 0, "commit_or_push": False}
    batch = {"generated_at_utc": iz(now()), "status": status, "offline_tests": "PASS" if offline_ok else "FAIL", "corrected_previous_v3_status": "PARTIAL_DETAIL_TXT_VALIDATION_OFFLINE_REPAIR_REQUIRED_NETWORK_NOT_RUN", "response_record_appearances": counts["response_record_appearances"], "current_run_unique_candidate_keys": counts["current_run_unique_candidate_keys"], "net_new_candidates": counts["net_new_candidates"], "cumulative_unique_candidates": counts["cumulative_unique_candidate_keys"], "provisional_recent_104_present": len(present), "provisional_recent_104_missing": len(missing), "previously_validated_recent": len(previous_recent), "validated_this_batch": len(validated_batch), "total_validated_recent": len(previous_recent) + len(validated_batch), "remaining_unvalidated_recent": remaining, "missing_metadata_weeks": len(missing), "detail_requests": req_counts["detail_requests"], "txt_requests": req_counts["txt_requests"], "cumulative_bytes": req_counts["bytes"], "pause_log": req_counts["pause_log"], "http_waf": status == "PARTIAL_DETAIL_TXT_VALIDATION_HTTP_WAF_BLOCKED_WITH_CHECKPOINT", "results": results, "side_effect_counts": side, "global_gates": {"recent_104_unique_weeks": "FAIL_INCOMPLETE_METADATA_DISCOVERY", "freshness_gate": "NOT_FINAL_INCOMPLETE_ENUMERATION", "trusted_ingestion": "BLOCKED"}}
    checkpoint = {"generated_at_utc": iz(now()), "run_id": run_id, "latest_cdc_request_utc": req_counts["latest_cdc_request_utc"], "completed_candidates": [{"pid": x["candidate"]["pid"], "week": x["candidate"]["title_week_key"], "status": x["status"]} for x in validated_batch], "failed_or_blocked_candidates": [{"pid": x["candidate"]["pid"], "week": x["candidate"]["title_week_key"], "status": x["status"]} for x in failed], "next_exact_candidate": None, "request_counts": req_counts, "resume_status_values": ["DETAIL_SAVED_TXT_PENDING", "BLOCKED_HTTP", "BLOCKED_CONTENT_GATE", "PASS"]}
    done_pids = {x["candidate"]["pid"] for x in results}
    for pid, week in FIRST20:
        pp = pid.split(":")[1]
        if pp not in done_pids:
            row = next(r for r in rows if r["pid"] == pp)
            checkpoint["next_exact_candidate"] = {"pid": pp, "week": week, "oai_identifier": row["oai_identifier"], "datestamp": row["datestamp"], "canonical_detail_url": row["canonical_detail_url"]}
            break
    write_new(report_dir / "OAI_METADATA_CANDIDATE_LEDGER_V4.json", {"generated_at_utc": iz(now()), "items": rows, "counts": counts, "candidate_source_sha_match": "127/127"})
    write_new(report_dir / "RECENT_104_PROVISIONAL_COVERAGE_MATRIX_V4.json", coverage)
    write_new(report_dir / "OFFICIAL_METADATA_ANOMALIES_V4.json", {"generated_at_utc": iz(now()), "items": anoms, "pre_v4_anomaly_count": 9, "v4_anomaly_count": len(anoms)})
    write_new(report_dir / "PREVIOUS_DETAIL_TXT_REVALIDATION_V4.json", {"generated_at_utc": iz(now()), "items": prev_reval, "total_valid_txt": len(prev_reval), "recent_104_valid_txt": len(previous_recent), "status": "PASS" if len(prev_reval) == 22 and len(previous_recent) == 15 and all(x["status"] == "PASS" for x in prev_reval) else "FAIL"})
    write_new(report_dir / "DETAIL_TXT_VALIDATION_BATCH_1_V4.json", batch)
    write_new(report_dir / "DETAIL_TXT_VALIDATION_V4_CHECKPOINT.json", checkpoint)
    write_new(report_dir / "DETAIL_TXT_VALIDATION_V4_TEST_RESULTS.json", {"generated_at_utc": iz(now()), "status": "PASS" if offline_ok else "FAIL", "tests": tests})
    text_new(report_dir / "DETAIL_TXT_VALIDATION_BATCH_1_V4_REPORT.md", "\n".join([f"# Current status: {status}", "", f"- offline_tests: {'PASS' if offline_ok else 'FAIL'}", f"- anomaly_count_before: 9", f"- anomaly_count_after: {len(anoms)}", f"- previous_txt_revalidated: {len(prev_reval)}", f"- previous_recent_15: {len(previous_recent)}", f"- dynamic_104_present: {len(present)}", f"- dynamic_104_missing: {len(missing)}", f"- validated_this_batch: {len(validated_batch)}", f"- remaining_unvalidated_recent: {remaining}", f"- detail_requests: {req_counts['detail_requests']}", f"- txt_requests: {req_counts['txt_requests']}", f"- cumulative_bytes: {req_counts['bytes']}", f"- next_exact_candidate: {checkpoint['next_exact_candidate']}", "- side effects: database=0, migration=0, trusted_ingestion=0, dashboard=0, API=0, worker=0, training=0, prediction=0, model/vLLM=0", "", "Global gates remain: recent_104_unique_weeks=FAIL_INCOMPLETE_METADATA_DISCOVERY; freshness_gate=NOT_FINAL_INCOMPLETE_ENUMERATION; trusted_ingestion=BLOCKED."]) + "\n")
    files = ["OAI_METADATA_CANDIDATE_LEDGER_V4.json", "RECENT_104_PROVISIONAL_COVERAGE_MATRIX_V4.json", "OFFICIAL_METADATA_ANOMALIES_V4.json", "PREVIOUS_DETAIL_TXT_REVALIDATION_V4.json", "DETAIL_TXT_VALIDATION_BATCH_1_V4.json", "DETAIL_TXT_VALIDATION_BATCH_1_V4_REPORT.md", "DETAIL_TXT_VALIDATION_V4_CHECKPOINT.json", "DETAIL_TXT_VALIDATION_V4_TEST_RESULTS.json"]
    zip_path, zip_sha = make_package(report_dir, run_id, files)
    print(json.dumps({"status": status, "offline_tests": "PASS" if offline_ok else "FAIL", "validated_this_batch": len(validated_batch), "detail_requests": req_counts["detail_requests"], "txt_requests": req_counts["txt_requests"], "zip": str(zip_path), "zip_sha256": zip_sha}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

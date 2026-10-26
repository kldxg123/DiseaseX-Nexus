from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)
ART = REPORT_DIR / "artifacts" / "cdc_stacks_oai_export_probe"
ART.mkdir(parents=True, exist_ok=True)

OAI_URLS = [
    "https://stacks.cdc.gov/fedora/oai?verb=Identify",
    "https://stacks.cdc.gov/fedora/oai?verb=ListMetadataFormats",
    "https://stacks.cdc.gov/fedora/oai?verb=ListSets",
]
EXPORT_URLS = [
    "https://stacks.cdc.gov/fedora/export/download/collection/cdc%3A49375?rows=1&start=0",
    "https://stacks.cdc.gov/fedora/export/download/collection/csv/cdc%3A49375?rows=1&start=0",
    "https://stacks.cdc.gov/fedora/export/download/collection/xmlFormat/cdc%3A49375?rows=1&start=0",
]

NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sample_text(data: bytes, limit: int = 2000) -> str:
    text = data.decode("utf-8", "replace")
    # Remove accidental long whitespace and keep only a bounded sample.
    return re.sub(r"\s+", " ", text[:limit]).strip()


def safe_headers(headers: Any) -> dict[str, str]:
    keep = {"content-type", "etag", "last-modified", "cache-control", "date", "server", "location"}
    return {k: v for k, v in dict(headers.items()).items() if k.lower() in keep}


def fetch(url: str, *, method: str = "GET", retry_once: bool = False) -> dict[str, Any]:
    attempts = 2 if retry_once else 1
    last: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        fetched_at = utc_now()
        req = urllib.request.Request(
            url,
            method=method,
            headers={
                "User-Agent": "epidemic-openclaw-cdc-stacks-probe/1.0",
                "Accept": "*/*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = resp.read(5_000_000)
                status: int | str = resp.status
                final_url = resp.geturl()
                headers = safe_headers(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read(1_000_000)
            status = exc.code
            final_url = exc.geturl()
            headers = safe_headers(exc.headers)
        except Exception as exc:
            last = {
                "request_url": url,
                "method": method,
                "attempt": attempt,
                "fetched_at_utc": fetched_at,
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
            }
            if attempt < attempts:
                time.sleep(1.5 * attempt)
                continue
            return last

        digest = hashlib.sha256(body).hexdigest()
        suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", urllib.parse.urlparse(url).path.strip("/") or "root")[:80]
        raw_name = f"{digest[:12]}_{status}_{suffix}.bin"
        raw_path = ART / raw_name
        raw_path.write_bytes(body)
        last = {
            "request_url": url,
            "method": method,
            "attempt": attempt,
            "fetched_at_utc": fetched_at,
            "status": status,
            "final_url": final_url,
            "headers": headers,
            "body_size_bytes": len(body),
            "body_sha256": digest,
            "raw_file": str(raw_path.relative_to(REPORT_DIR)),
            "body_sample_redacted": sample_text(body),
            "is_html_block_or_error": b"<html" in body[:500].lower() or b"forbidden" in body[:2000].lower(),
        }
        if status not in {429, 500, 502, 503, 504}:
            return last
        if attempt < attempts:
            time.sleep(1.5 * attempt)
    return last or {"request_url": url, "status": "ERROR", "error_type": "NO_RESULT"}


def parse_oai(record: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    raw_file = record.get("raw_file")
    if not raw_file:
        return False, {"valid_xml": False, "reason": "NO_BODY"}
    data = (REPORT_DIR / raw_file).read_bytes()
    try:
        root = ET.fromstring(data)
    except Exception as exc:
        return False, {"valid_xml": False, "reason": type(exc).__name__}
    valid = root.tag.endswith("OAI-PMH")
    errors = [
        {
            "code": e.attrib.get("code"),
            "text": "".join(e.itertext()).strip(),
        }
        for e in root.findall("oai:error", NS)
    ]
    info: dict[str, Any] = {
        "valid_xml": True,
        "valid_oai_pmh": valid,
        "root_tag": root.tag,
        "errors": errors,
    }
    identify = root.find("oai:Identify", NS)
    if identify is not None:
        info["repository_name"] = (identify.findtext("oai:repositoryName", default="", namespaces=NS) or "").strip()
        info["base_url"] = (identify.findtext("oai:baseURL", default="", namespaces=NS) or "").strip()
        info["granularity"] = (identify.findtext("oai:granularity", default="", namespaces=NS) or "").strip()
    formats = []
    for fmt in root.findall(".//oai:metadataFormat", NS):
        formats.append({
            "metadataPrefix": (fmt.findtext("oai:metadataPrefix", default="", namespaces=NS) or "").strip(),
            "schema": (fmt.findtext("oai:schema", default="", namespaces=NS) or "").strip(),
            "metadataNamespace": (fmt.findtext("oai:metadataNamespace", default="", namespaces=NS) or "").strip(),
        })
    if formats:
        info["metadata_formats"] = formats
    sets = []
    for st in root.findall(".//oai:set", NS):
        sets.append({
            "setSpec": (st.findtext("oai:setSpec", default="", namespaces=NS) or "").strip(),
            "setName": (st.findtext("oai:setName", default="", namespaces=NS) or "").strip(),
        })
    if sets:
        info["sets"] = sets[:500]
        info["set_count_sampled"] = len(sets)
    tokens = []
    for tok in root.findall(".//oai:resumptionToken", NS):
        tokens.append({"text_present": bool((tok.text or "").strip()), "attributes": dict(tok.attrib)})
    if tokens:
        info["resumption_tokens"] = tokens
    headers = []
    for hdr in root.findall(".//oai:header", NS):
        headers.append({
            "identifier": (hdr.findtext("oai:identifier", default="", namespaces=NS) or "").strip(),
            "datestamp": (hdr.findtext("oai:datestamp", default="", namespaces=NS) or "").strip(),
            "setSpecs": [(x.text or "").strip() for x in hdr.findall("oai:setSpec", NS)],
        })
    if headers:
        info["record_headers_sample"] = headers[:20]
        info["record_header_count_sampled"] = len(headers)
    return bool(valid), info


def resolve_nndss_set(oai_infos: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for info in oai_infos:
        for st in info.get("sets", []) or []:
            name = st.get("setName", "")
            spec = st.get("setSpec", "")
            if "National Notifiable Diseases Surveillance System" in name or "cdc:49375" in spec or "49375" in spec:
                candidates.append(st)
    exact = [
        c for c in candidates
        if "National Notifiable Diseases Surveillance System" in c.get("setName", "")
        or c.get("setSpec") == "cdc:49375"
    ]
    chosen = exact[0] if exact else (candidates[0] if candidates else None)
    return {
        "status": "RESOLVED" if chosen else "NOT_RESOLVED",
        "target_collection_name": "National Notifiable Diseases Surveillance System (NNDSS)",
        "target_collection_pid": "cdc:49375",
        "candidates": candidates,
        "chosen_set": chosen,
        "setSpec_source": "ListSets" if chosen else None,
    }


def oai_listrecords_probe(set_spec: str, granularity: str | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=45)
    if granularity == "YYYY-MM-DD":
        from_v = start.date().isoformat()
        until_v = now.date().isoformat()
    else:
        from_v = start.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        until_v = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params = urllib.parse.urlencode({
        "verb": "ListRecords",
        "metadataPrefix": "oai_dc",
        "set": set_spec,
        "from": from_v,
        "until": until_v,
    })
    url = f"https://stacks.cdc.gov/fedora/oai?{params}"
    rec = fetch(url)
    valid, info = parse_oai(rec)
    rec["valid_oai_pmh_xml"] = valid
    rec["parsed"] = info
    rec["bounded_datestamp_window"] = {"from": from_v, "until": until_v, "note": "OAI-PMH repository datestamp, not case/MMWR week"}
    return rec


def classify_export(record: dict[str, Any]) -> dict[str, Any]:
    raw_file = record.get("raw_file")
    if not raw_file:
        return {"format_valid": False, "reason": "NO_BODY"}
    data = (REPORT_DIR / raw_file).read_bytes()
    ctype = (record.get("headers") or {}).get("Content-Type") or (record.get("headers") or {}).get("content-type") or ""
    text = data.decode("utf-8", "replace")
    if record.get("is_html_block_or_error"):
        return {"format_valid": False, "reason": "HTML_BLOCK_OR_ERROR_PAGE", "content_type": ctype}
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(text)
            return {
                "format_valid": True,
                "format": "json",
                "content_type": ctype,
                "top_level_type": type(parsed).__name__,
                "has_cdc_pid": "cdc:" in text,
                "has_canonical_url": "stacks.cdc.gov/view/cdc/" in text,
                "has_collection_pid": "cdc:49375" in text,
            }
        except Exception as exc:
            return {"format_valid": False, "reason": f"JSON_{type(exc).__name__}", "content_type": ctype}
    if stripped.startswith("<"):
        try:
            ET.fromstring(data)
            return {
                "format_valid": True,
                "format": "xml",
                "content_type": ctype,
                "has_cdc_pid": "cdc:" in text,
                "has_canonical_url": "stacks.cdc.gov/view/cdc/" in text,
                "has_collection_pid": "cdc:49375" in text,
            }
        except Exception as exc:
            return {"format_valid": False, "reason": f"XML_{type(exc).__name__}", "content_type": ctype}
    try:
        rows = list(csv.reader(io.StringIO(text[:100000])))
        return {
            "format_valid": len(rows) > 0 and len(rows[0]) > 1,
            "format": "csv",
            "content_type": ctype,
            "header": rows[0] if rows else [],
            "first_row": rows[1] if len(rows) > 1 else [],
            "has_cdc_pid": "cdc:" in text,
            "has_canonical_url": "stacks.cdc.gov/view/cdc/" in text,
            "has_collection_pid": "cdc:49375" in text,
        }
    except Exception as exc:
        return {"format_valid": False, "reason": f"CSV_{type(exc).__name__}", "content_type": ctype}


def main() -> None:
    oai_records = []
    oai_infos = []
    for url in OAI_URLS:
        rec = fetch(url)
        valid, info = parse_oai(rec)
        rec["valid_oai_pmh_xml"] = valid
        rec["parsed"] = info
        oai_records.append(rec)
        oai_infos.append(info)
        time.sleep(1.0)
    set_resolution = resolve_nndss_set(oai_infos)
    if set_resolution["status"] == "RESOLVED":
        granularity = next((i.get("granularity") for i in oai_infos if i.get("granularity")), None)
        set_resolution["list_records_probe"] = oai_listrecords_probe(set_resolution["chosen_set"]["setSpec"], granularity)
        time.sleep(1.0)

    (REPORT_DIR / "CDC_STACKS_OAI_PMH_PROBE.json").write_text(
        json.dumps({
            "generated_at_utc": utc_now(),
            "mode": "READ_ONLY_OAI_PMH_PROBE",
            "requests": oai_records,
        }, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (REPORT_DIR / "CDC_STACKS_OAI_SET_RESOLUTION.json").write_text(
        json.dumps(set_resolution, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    exports = []
    for url in EXPORT_URLS:
        rec = fetch(url, retry_once=True)
        rec["format_validation"] = classify_export(rec)
        exports.append(rec)
        time.sleep(1.0)
    (REPORT_DIR / "CDC_STACKS_COLLECTION_EXPORT_PROBE.json").write_text(
        json.dumps({
            "generated_at_utc": utc_now(),
            "mode": "READ_ONLY_COLLECTION_EXPORT_PROBE",
            "requests": exports,
        }, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(json.dumps({
        "oai_statuses": [r.get("status") for r in oai_records],
        "oai_valid": [r.get("valid_oai_pmh_xml") for r in oai_records],
        "set_resolution": set_resolution.get("status"),
        "export_statuses": [r.get("status") for r in exports],
        "export_valid": [r.get("format_validation", {}).get("format_valid") for r in exports],
    }, sort_keys=True))


if __name__ == "__main__":
    main()

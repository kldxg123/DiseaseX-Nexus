from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)
ART = REPORT_DIR / "artifacts" / "cdc_stacks_oai_global_canary"
ART.mkdir(parents=True, exist_ok=True)
OUT = REPORT_DIR / "CDC_STACKS_OAI_GLOBAL_LISTRECORDS_CANARY.json"

CANARY_URL = "https://stacks.cdc.gov/fedora/oai?verb=ListRecords&metadataPrefix=oai_dc&from=2025-10-17T00%3A00%3A00Z&until=2025-10-17T23%3A59%3A59Z"
WIDE_URL = "https://stacks.cdc.gov/fedora/oai?verb=ListRecords&metadataPrefix=oai_dc&from=2025-10-01T00%3A00%3A00Z&until=2025-10-31T23%3A59%3A59Z"
OAI_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def headers_dict(headers: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(headers.items()).items()}


def sample_text(data: bytes, limit: int = 2500) -> str:
    return re.sub(r"\s+", " ", data.decode("utf-8", "replace")[:limit]).strip()


def fetch(url: str) -> dict[str, Any]:
    fetched_at = utc_now()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "epidemic-openclaw-oai-probe/1.0",
            "Accept": "application/xml,text/xml,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read(10_000_001)
            status: int | str = resp.status
            final_url = resp.geturl()
            headers = headers_dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read(2_000_000)
        status = exc.code
        final_url = exc.geturl()
        headers = headers_dict(exc.headers)
    except Exception as exc:
        return {
            "request_url": url,
            "fetched_at_utc": fetched_at,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
        }
    digest = hashlib.sha256(body).hexdigest()
    raw = ART / f"{digest[:12]}_{status}.xml"
    raw.write_bytes(body)
    result: dict[str, Any] = {
        "request_url": url,
        "fetched_at_utc": fetched_at,
        "status": status,
        "final_url": final_url,
        "headers": headers,
        "content_type": headers.get("Content-Type") or headers.get("content-type"),
        "body_size_bytes": len(body),
        "body_sha256": digest,
        "raw_file": str(raw.relative_to(REPORT_DIR)),
        "body_sample_redacted": sample_text(body),
        "truncated_by_probe_limit": len(body) > 10_000_000,
        "html_or_block_page": b"<html" in body[:500].lower() or b"forbidden" in body[:2000].lower(),
    }
    result.update(parse_oai(body))
    return result


def parse_oai(body: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(body)
    except Exception as exc:
        return {
            "xml_valid": False,
            "oai_pmh_valid": False,
            "xml_error_type": type(exc).__name__,
            "oai_error_code": None,
            "record_count": 0,
            "records": [],
            "resumption_token": None,
        }
    oai_valid = root.tag.endswith("OAI-PMH")
    errors = root.findall("oai:error", OAI_NS)
    error_objs = [{"code": e.attrib.get("code"), "text": "".join(e.itertext()).strip()} for e in errors]
    records = []
    for rec in root.findall(".//oai:record", OAI_NS):
        hdr = rec.find("oai:header", OAI_NS)
        meta = rec.find("oai:metadata", OAI_NS)
        dc = meta.find(".//{http://purl.org/dc/elements/1.1/}dc") if meta is not None else None
        fields: dict[str, list[str]] = {}
        if dc is not None:
            for child in list(dc):
                tag = child.tag.split("}", 1)[-1]
                fields.setdefault(tag, []).append((child.text or "").strip())
        records.append(
            {
                "identifier": (hdr.findtext("oai:identifier", default="", namespaces=OAI_NS) if hdr is not None else "").strip(),
                "datestamp": (hdr.findtext("oai:datestamp", default="", namespaces=OAI_NS) if hdr is not None else "").strip(),
                "setSpecs": [(x.text or "").strip() for x in hdr.findall("oai:setSpec", OAI_NS)] if hdr is not None else [],
                "dc_title": fields.get("title", []),
                "dc_identifier": fields.get("identifier", []),
                "dc_source": fields.get("source", []),
                "dc_relation": fields.get("relation", []),
            }
        )
    token = root.find(".//oai:resumptionToken", OAI_NS)
    return {
        "xml_valid": True,
        "oai_pmh_valid": oai_valid,
        "oai_errors": error_objs,
        "oai_error_code": error_objs[0]["code"] if error_objs else None,
        "record_count": len(records),
        "records": records[:100],
        "resumption_token": {
            "present": token is not None,
            "text_present": bool((token.text or "").strip()) if token is not None else False,
            "attributes": dict(token.attrib) if token is not None else {},
        },
    }


def main() -> None:
    requests = []
    first = fetch(CANARY_URL)
    requests.append(first)
    if first.get("status") == 200 and first.get("oai_pmh_valid") and first.get("oai_error_code") == "noRecordsMatch":
        time.sleep(3)
        requests.append(fetch(WIDE_URL))
    payload = {
        "generated_at_utc": utc_now(),
        "mode": "READ_ONLY_GLOBAL_LISTRECORDS_CANARY",
        "canary_reason": "2025-10-17 chosen because existing official collection export evidence had cdc:232532 fgs.lastModifiedDate=2025-10-17T15:33:11.307Z.",
        "requests": requests,
        "decision": {
            "global_listrecords_usable": any(r.get("status") == 200 and r.get("oai_pmh_valid") and r.get("record_count", 0) > 0 for r in requests),
            "blocked_by_http_or_html": any(r.get("status") == 403 or r.get("html_or_block_page") for r in requests),
            "only_no_records_match": all(r.get("oai_error_code") == "noRecordsMatch" for r in requests if r.get("oai_pmh_valid")),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "statuses": [r.get("status") for r in requests], "records": [r.get("record_count") for r in requests], "errors": [r.get("oai_error_code") for r in requests], "usable": payload["decision"]["global_listrecords_usable"]}, sort_keys=True))


if __name__ == "__main__":
    main()

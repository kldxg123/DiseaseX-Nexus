from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)


def read_json(name: str) -> dict:
    p = REPORT_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_json(name: str, payload: dict) -> None:
    (REPORT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_files() -> list[dict]:
    files = []
    for path in REPORT_DIR.rglob("*"):
        if path.is_file():
            rel = path.relative_to(REPORT_DIR).as_posix()
            if rel == "PACKAGE_MANIFEST.json":
                continue
            files.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha(path)})
    return sorted(files, key=lambda x: x["path"])


def main() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    oai = read_json("CDC_STACKS_OAI_PMH_PROBE.json")
    setres = read_json("CDC_STACKS_OAI_SET_RESOLUTION.json")
    export = read_json("CDC_STACKS_COLLECTION_EXPORT_PROBE.json")
    rows = read_json("CDC_STACKS_COLLECTION_EXPORT_ROWS_LIMIT_PROBE.json")
    enum = read_json("US_LEGIONELLOSIS_STACKS_ENUMERATION_MANIFEST.json")

    matrix = {
        "generated_at_utc": now,
        "previous_status_preserved": "PARTIAL_BLOCKED_CDC_STACKS_CBROWSE_403",
        "endpoints": {
            "oai_identify": {
                "status": (oai.get("requests") or [{}])[0].get("status"),
                "valid_oai_xml": (oai.get("requests") or [{}])[0].get("valid_oai_pmh_xml"),
                "usable_for_collection_enumeration": False,
                "reason": "Identify only; not collection enumeration.",
            },
            "oai_list_metadata_formats": {
                "status": (oai.get("requests") or [{}, {}])[1].get("status") if len(oai.get("requests", [])) > 1 else None,
                "valid_oai_xml": (oai.get("requests") or [{}, {}])[1].get("valid_oai_pmh_xml") if len(oai.get("requests", [])) > 1 else None,
                "usable_for_collection_enumeration": False,
                "reason": "Format discovery only.",
            },
            "oai_list_sets": {
                "status": (oai.get("requests") or [{}, {}, {}])[2].get("status") if len(oai.get("requests", [])) > 2 else None,
                "valid_oai_xml": (oai.get("requests") or [{}, {}, {}])[2].get("valid_oai_pmh_xml") if len(oai.get("requests", [])) > 2 else None,
                "set_resolution": setres.get("status"),
                "usable_for_collection_enumeration": setres.get("status") == "RESOLVED",
                "reason": "NNDSS/cdc:49375 setSpec was not returned by ListSets; setSpec was not guessed.",
            },
            "collection_export_initial_json_csv_xml": [
                {
                    "url": r.get("request_url"),
                    "status": r.get("status"),
                    "format_valid": r.get("format_validation", {}).get("format_valid"),
                    "format": r.get("format_validation", {}).get("format"),
                    "usable_for_single_record_probe": r.get("status") == 200 and r.get("format_validation", {}).get("format_valid"),
                }
                for r in export.get("requests", [])
            ],
            "collection_export_rows_limit_probe": rows.get("results", []),
            "limited_enumeration_attempt": {
                "status": "BLOCKED",
                "candidate_count_total": enum.get("candidate_count_total"),
                "downloaded_count": enum.get("downloaded_count"),
                "first_page_status": (enum.get("pages") or [{}])[0].get("status") if enum.get("pages") else None,
                "reason": "rows=500 pagination returned HTTP 403; subsequent explicit rows probes also returned HTTP 403.",
            },
        },
        "overall": {
            "status": "PARTIAL_BLOCKED_CDC_COLLECTION_EXPORT_PAGINATION_403_OAI_SET_NOT_RESOLVED",
            "database_writes": 0,
            "trusted_ingestion": 0,
            "training_started": False,
            "prediction_started": False,
            "active_pointer_modified": False,
        },
    }
    write_json("CDC_ENUMERATION_ENDPOINT_MATRIX.json", matrix)

    (REPORT_DIR / "OFFLINE_OFFICIAL_SOURCE_BUNDLE_REQUIREMENTS.md").write_text(
        "# Offline official source bundle requirements\n\n"
        "The current vm201 network cannot complete CDC Stacks collection enumeration.\n\n"
        "Minimum unblock options:\n\n"
        "1. Network administrator allows vm201 to access these CDC official endpoints without 403:\n"
        "   - `https://data.cdc.gov/api/views/x9gk-5huc`\n"
        "   - `https://data.cdc.gov/resource/x9gk-5huc.json`\n"
        "   - `https://stacks.cdc.gov/cbrowse?parentId=cdc%3A49375&terms=legionellosis&sortBy=pub_date_key+asc&start=0`\n"
        "   - `https://stacks.cdc.gov/fedora/export/download/collection/cdc%3A49375?rows=<bounded>&start=<bounded>`\n\n"
        "2. User downloads original official CDC export files from CDC pages and uploads them to vm201. The bundle must include:\n"
        "   - Original CDC URL for each file.\n"
        "   - UTC download time.\n"
        "   - File size in bytes.\n"
        "   - SHA-256 computed before upload and after upload.\n"
        "   - No edited, re-encoded, third-party mirrored, or OCR-derived replacements.\n\n"
        "Do not use proxies, third-party mirrors, search-engine snippets, guessed CDC object IDs, or TLS bypass.\n",
        encoding="utf-8",
    )

    status = "PARTIAL_BLOCKED_CDC_COLLECTION_EXPORT_PAGINATION_403_OAI_SET_NOT_RESOLVED"
    summary = {
        "status": status,
        "generated_at_utc": now,
        "previous_status_preserved": "PARTIAL_BLOCKED_CDC_STACKS_CBROWSE_403",
        "oai_pmh": "BASE_ENDPOINTS_200_VALID_XML_BUT_NNDSS_SET_NOT_RESOLVED",
        "collection_export": "INITIAL_ROWS1_JSON_CSV_XML_200_VALID_BUT_PAGINATION_ROWS_PROBES_403",
        "trusted_ingestion_run": False,
        "database_writes": 0,
        "migration_run": False,
        "active_pointer_modified": False,
        "training_started": False,
        "prediction_started": False,
        "commit_or_push": False,
        "minimum_unblock": "Allow stable bounded CDC Stacks collection export pagination or provide/upload an official CDC source bundle with URL, download time, file size and SHA-256.",
    }
    write_json("FINAL_SUMMARY.json", summary)
    (REPORT_DIR / "FINAL_REPORT.md").write_text(
        "# Final Report: CDC OAI-PMH and collection export probe\n\n"
        f"Final status: `{status}`\n\n"
        "This run did not redo the M0 backup and did not modify the existing backup.\n\n"
        "## OAI-PMH\n\n"
        "- `Identify`: 200, valid OAI-PMH XML.\n"
        "- `ListMetadataFormats`: 200, valid OAI-PMH XML.\n"
        "- `ListSets`: 200, valid OAI-PMH XML, but no NNDSS / `cdc:49375` setSpec was resolved. I did not guess or construct a setSpec.\n\n"
        "## Collection export\n\n"
        "- Initial JSON/CSV/XML `rows=1&start=0` probes returned 200 and valid machine-readable formats.\n"
        "- Limited enumeration using larger bounded pages was blocked: `rows=500&start=0` returned 403.\n"
        "- Explicit rows limit probes for 1/2/5/10/20/25/50/100/200 subsequently returned 403.\n\n"
        "## Why execution stopped\n\n"
        "The official endpoints were partially reachable but did not provide a stable complete enumeration path: OAI did not expose the required setSpec, and collection export pagination returned 403. "
        "Therefore I did not fetch a complete 104-week official history, did not parse trusted data, did not write database rows, and did not run forecasts.\n\n"
        "## Mutation summary\n\n"
        "- Database writes: 0\n"
        "- Migrations: 0\n"
        "- Trusted ingestion: 0\n"
        "- Active pointer changes: 0\n"
        "- Training/prediction/model calls: 0\n"
        "- Commit/push: none\n\n"
        "See `CDC_ENUMERATION_ENDPOINT_MATRIX.json` and `OFFLINE_OFFICIAL_SOURCE_BUNDLE_REQUIREMENTS.md` for exact evidence and unblock options.\n",
        encoding="utf-8",
    )
    write_json("PACKAGE_MANIFEST.json", {"generated_at_utc": now, "files": collect_files()})
    zip_path = REPORT_DIR.with_suffix(".zip")
    subprocess.run(["python3", "-m", "zipfile", "-c", str(zip_path), str(REPORT_DIR)], check=True)
    subprocess.run(["python3", "-m", "zipfile", "-l", str(zip_path)], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({"status": status, "report_dir": str(REPORT_DIR), "zip": str(zip_path)}, sort_keys=True))


if __name__ == "__main__":
    main()

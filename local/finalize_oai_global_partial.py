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
    rows = []
    for path in REPORT_DIR.rglob("*"):
        if path.is_file():
            rel = path.relative_to(REPORT_DIR).as_posix()
            if rel == "PACKAGE_MANIFEST.json":
                continue
            rows.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha(path)})
    return sorted(rows, key=lambda x: x["path"])


def main() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    enum = read_json("CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION.json")
    canary = read_json("CDC_STACKS_OAI_GLOBAL_LISTRECORDS_CANARY.json")
    gate = enum.get("gate_status", {})
    status = "PARTIAL_OAI_GLOBAL_ENUMERATION_INSUFFICIENT_104_WEEK_COVERAGE"
    if enum.get("pages") and any(p.get("status") == 403 for p in enum.get("pages", [])):
        status = "PARTIAL_BLOCKED_CDC_OAI_GLOBAL_LISTRECORDS_AND_EXPORT_PAGINATION"

    matrix = read_json("CDC_ENUMERATION_ENDPOINT_MATRIX.json")
    matrix["oai_global_listrecords_canary"] = {
        "status": "PASS" if canary.get("decision", {}).get("global_listrecords_usable") else "FAIL",
        "records": [r.get("record_count") for r in canary.get("requests", [])],
        "errors": [r.get("oai_error_code") for r in canary.get("requests", [])],
    }
    matrix["oai_global_bounded_enumeration"] = {
        "status": "PASS_ENUMERATED_BUT_INSUFFICIENT_COVERAGE" if gate.get("oai_global_enumeration") == "PASS" else "FAIL",
        "stop_reason": enum.get("stop_reason"),
        "pages": len(enum.get("pages", [])),
        "dedup_record_count": enum.get("dedup_record_count"),
        "metadata_candidate_count": enum.get("metadata_candidate_count"),
        "validated_candidate_count": enum.get("validated_candidate_count"),
        "txt_valid_count": enum.get("week_matrix", {}).get("txt_valid_count"),
        "minimum_104_records": gate.get("minimum_104_records"),
        "txt_validation": gate.get("txt_validation"),
    }
    matrix["overall"] = {
        "status": status,
        "database_writes": 0,
        "trusted_ingestion": 0,
        "training_started": False,
        "prediction_started": False,
        "active_pointer_modified": False,
    }
    write_json("CDC_ENUMERATION_ENDPOINT_MATRIX.json", matrix)
    summary = {
        "status": status,
        "generated_at_utc": now,
        "previous_status_preserved": "PARTIAL_BLOCKED_CDC_COLLECTION_EXPORT_PAGINATION_403_OAI_SET_NOT_RESOLVED",
        "canary": "PASS_100_RECORDS",
        "bounded_enumeration": {
            "stop_reason": enum.get("stop_reason"),
            "pages": len(enum.get("pages", [])),
            "dedup_record_count": enum.get("dedup_record_count"),
            "metadata_candidate_count": enum.get("metadata_candidate_count"),
            "validated_candidate_count": enum.get("validated_candidate_count"),
            "txt_valid_count": enum.get("week_matrix", {}).get("txt_valid_count"),
            "minimum_104_records": gate.get("minimum_104_records"),
            "txt_validation": gate.get("txt_validation"),
        },
        "phase_a_passed": False,
        "why_not_ingested": "OAI global enumeration found some official Legionellosis metadata, but not enough validated TXT records to satisfy the recent 104-week completeness gate; one candidate TXT validation also failed.",
        "database_writes": 0,
        "migration_run": False,
        "trusted_ingestion_run": False,
        "active_pointer_modified": False,
        "training_started": False,
        "prediction_started": False,
        "commit_or_push": False,
        "minimum_unblock": "Continue/resume OAI global enumeration with a larger authorized time/page budget, or provide/upload an official CDC source bundle with URL, download time, file size and SHA-256. Do not guess PID/DS2 filenames.",
    }
    write_json("FINAL_SUMMARY.json", summary)
    (REPORT_DIR / "FINAL_REPORT.md").write_text(
        "# Final Report: No-set OAI-PMH ListRecords probe and bounded enumeration\n\n"
        f"Final status: `{status}`\n\n"
        "## Canary\n\n"
        "- No-set `ListRecords` canary for `2025-10-17T00:00:00Z` to `2025-10-17T23:59:59Z` returned HTTP 200, legal OAI-PMH XML, and 100 records.\n"
        "- This proves global no-set OAI harvesting is usable; `ListSets` not resolving NNDSS does not by itself block global harvesting.\n\n"
        "## Bounded enumeration\n\n"
        f"- Range: `{enum.get('range', {}).get('from')}` to `{enum.get('range', {}).get('until')}` repository datestamp, not case/MMWR week.\n"
        f"- Stop reason: `{enum.get('stop_reason')}`.\n"
        f"- Pages fetched: `{len(enum.get('pages', []))}`.\n"
        f"- Deduplicated OAI records: `{enum.get('dedup_record_count')}`.\n"
        f"- Legionellosis metadata candidates: `{enum.get('metadata_candidate_count')}`.\n"
        f"- Detail-page validated candidates: `{enum.get('validated_candidate_count')}`.\n"
        f"- TXT valid candidates: `{enum.get('week_matrix', {}).get('txt_valid_count')}`.\n"
        f"- 104-week gate: `{gate.get('minimum_104_records')}`.\n"
        f"- TXT validation gate: `{gate.get('txt_validation')}`.\n\n"
        "## Why execution stopped\n\n"
        "Phase A did not pass because the OAI bounded enumeration did not produce at least 104 validated weekly TXT records. "
        "Missing weeks were not converted to zero, and no guessed CDC PID, OAI identifier, DS2 filename or setSpec was used. "
        "Because Phase A did not pass, trusted ingestion, Dashboard publication and all forecasting stages were not executed.\n\n"
        "## Mutation summary\n\n"
        "- Database writes: 0\n"
        "- Migrations: 0\n"
        "- Trusted ingestion: 0\n"
        "- Active pointer changes: 0\n"
        "- Training/prediction/model calls: 0\n"
        "- Commit/push: none\n\n"
        "See `CDC_STACKS_OAI_GLOBAL_LISTRECORDS_CANARY.json`, `CDC_STACKS_OAI_GLOBAL_BOUNDED_ENUMERATION.json`, and `CDC_ENUMERATION_ENDPOINT_MATRIX.json` for machine-readable evidence.\n",
        encoding="utf-8",
    )
    write_json("PACKAGE_MANIFEST.json", {"generated_at_utc": now, "files": collect_files()})
    zip_path = REPORT_DIR.with_suffix(".zip")
    subprocess.run(["python3", "-m", "zipfile", "-c", str(zip_path), str(REPORT_DIR)], check=True)
    subprocess.run(["python3", "-m", "zipfile", "-l", str(zip_path)], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({"status": status, "report_dir": str(REPORT_DIR), "zip": str(zip_path)}, sort_keys=True))


if __name__ == "__main__":
    main()

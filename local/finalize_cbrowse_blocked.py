from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)


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
    cbrowse = json.loads((REPORT_DIR / "CDC_STACKS_CBROWSE_PROBE.json").read_text(encoding="utf-8"))
    statuses = [r.get("status") for r in cbrowse.get("requests", [])]
    summary = {
        "status": "PARTIAL_BLOCKED_CDC_STACKS_CBROWSE_403",
        "previous_status_preserved": "PARTIAL_BLOCKED_CDC_DATA_CDC_GOV_403",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "cdc_stacks_cbrowse_probe": "PASS_EVIDENCE_CAPTURED",
        "cbrowse_statuses": statuses,
        "blocked_reason": "CDC Stacks cbrowse official collection endpoints returned HTTP 403 from vm201 for both the general terms=legionellosis URL and the year-scoped 2026 URL.",
        "not_used_as_evidence": "old gsearch 403",
        "database_writes": 0,
        "migration_run": False,
        "trusted_ingestion_run": False,
        "active_pointer_modified": False,
        "training_started": False,
        "prediction_started": False,
        "commit_or_push": False,
        "minimum_unblock": "Allow vm201 access to CDC Stacks cbrowse collection pages or provide/authorize an official CDC Stacks enumeration API/URL list sufficient to enumerate complete Legionellosis weekly history without guessing CDC object IDs.",
    }
    write_json("FINAL_SUMMARY.json", summary)
    write_json("DATABASE_DELTA.json", {"status": "PASS_ZERO_MUTATION", "database_writes": 0, "migration_run": False})
    (REPORT_DIR / "FINAL_REPORT.md").write_text(
        "# Final Report: CDC Stacks cbrowse continuation\n\n"
        f"Final status: `{summary['status']}`\n\n"
        "The prior stage fact `PARTIAL_BLOCKED_CDC_DATA_CDC_GOV_403` is preserved. "
        "This continuation performed the newly authorized official CDC Stacks `cbrowse` probe and did not reuse `gsearch=403` as a substitute.\n\n"
        "## cbrowse evidence\n\n"
        "- General requested URL: `https://stacks.cdc.gov/cbrowse?parentId=cdc%3A49375&terms=legionellosis&sortBy=pub_date_key+asc&start=0`\n"
        "- Year-scoped requested URL: `https://stacks.cdc.gov/cbrowse?parentId=cdc%3A49375&terms=legionellosis&sortBy=pub_date_key+asc&ss_publishyear%5B0%5D=2026&start=0`\n"
        f"- Observed statuses: `{statuses}`\n"
        "- Detailed status, final URL, response headers, response SHA-256, and redacted body sample are saved in `CDC_STACKS_CBROWSE_PROBE.json`.\n\n"
        "## Why execution stopped\n\n"
        "Because both official `cbrowse` collection URLs returned HTTP 403 from vm201, complete official enumeration could not be performed. "
        "I did not guess CDC object IDs, did not use proxy or third-party mirrors, and did not disable TLS. "
        "Therefore trusted ingestion, active pointer updates, Dashboard publication, and all-series forecasting were not executed.\n\n"
        "## Mutation summary\n\n"
        "- Database writes: 0\n"
        "- Migrations: 0\n"
        "- Trusted ingestion: 0\n"
        "- Training/prediction/model calls: 0\n"
        "- Active pointer changes: 0\n"
        "- Commit/push: none\n",
        encoding="utf-8",
    )
    write_json("PACKAGE_MANIFEST.json", {"generated_at_utc": summary["generated_at_utc"], "files": collect_files()})
    zip_path = REPORT_DIR.with_suffix(".zip")
    subprocess.run(["python3", "-m", "zipfile", "-c", str(zip_path), str(REPORT_DIR)], check=True)
    subprocess.run(["python3", "-m", "zipfile", "-l", str(zip_path)], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({"status": summary["status"], "report_dir": str(REPORT_DIR), "zip": str(zip_path)}, sort_keys=True))


if __name__ == "__main__":
    main()

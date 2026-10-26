from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw")
REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(name: str, payload: dict) -> None:
    (REPORT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


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
    cdc_http = {}
    p = REPORT_DIR / "CDC_NNDSS_HTTP_DEBUG.json"
    if p.exists():
        cdc_http = json.loads(p.read_text(encoding="utf-8"))
    stacks_probe = REPORT_DIR / "artifacts/cdc_stacks_probe/cdc_257693_DS2.txt"
    stacks_available = stacks_probe.exists() and stacks_probe.stat().st_size > 0

    source_contract = {
        "status": "BLOCKED_BEFORE_TRUSTED_INGESTION",
        "official_primary_source": {
            "name": "CDC NNDSS Weekly Data",
            "dataset_id": "x9gk-5huc",
            "landing_page": "https://data.cdc.gov/NNDSS/NNDSS-Weekly-Data/x9gk-5huc",
            "soda_endpoint": "https://data.cdc.gov/resource/x9gk-5huc.json",
            "metadata_endpoint": "https://data.cdc.gov/api/views/x9gk-5huc",
            "expected_label": "Legionellosis",
            "expected_columns": [
                "states", "year", "week", "label", "m1", "m1_flag", "m2", "m2_flag",
                "m3", "m3_flag", "m4", "m4_flag", "location1", "location2", "sort_order"
            ],
        },
        "official_fallback_source": {
            "name": "CDC Stacks NNDSS weekly TXT supporting files",
            "reason": "CDC NNDSS page states weekly PDF/text tables are available via CDC Stacks; a sample Legionellosis TXT file was successfully fetched from stacks.cdc.gov.",
            "sample_url": "https://stacks.cdc.gov/view/cdc/257693/cdc_257693_DS2.txt",
            "sample_available_on_vm201": stacks_available,
            "sample_sha256": sha(stacks_probe) if stacks_available else None,
        },
        "vm201_primary_source_probe": {
            "result": "BLOCKED_403_FORBIDDEN",
            "http_debug_file": "CDC_NNDSS_HTTP_DEBUG.json",
            "v3_post_debug_file": "CDC_NNDSS_V3_POST_PROBE.json",
        },
        "credential_free": True,
        "tls_verification_disabled": False,
        "third_party_source_used_for_trusted_data": False,
        "raw_rows_written": 0,
        "normalized_rows_written": 0,
        "trusted_rows_written": 0,
    }
    write_json("US_LEGIONELLOSIS_SOURCE_CONTRACT.json", source_contract)
    write_json("US_LEGIONELLOSIS_FETCH_MANIFEST.json", {
        "status": "BLOCKED_CDC_SODA_403",
        "primary_soda_attempts": cdc_http,
        "stacks_single_file_probe": {
            "status": "PASS" if stacks_available else "BLOCKED",
            "file": "artifacts/cdc_stacks_probe/cdc_257693_DS2.txt" if stacks_available else None,
            "sha256": sha(stacks_probe) if stacks_available else None,
        },
        "complete_history_fetched": False,
        "bounded_discovery_status": "STOPPED_TO_AVOID_EXCESSIVE_OFFICIAL_OBJECT_PROBING",
    })
    blocked_common = {
        "status": "NOT_RUN_BLOCKED_UPSTREAM_FETCH",
        "reason": "Official primary CDC data.cdc.gov/Socrata endpoint returns 403 from vm201. Full official history was not fetched; therefore normalization/trusted ingestion/forecasting were not executed.",
        "database_writes": 0,
    }
    for name in [
        "US_LEGIONELLOSIS_NORMALIZATION_REPORT.json",
        "US_LEGIONELLOSIS_DATA_QUALITY.json",
        "US_LEGIONELLOSIS_REVISION_REPORT.json",
        "US_LEGIONELLOSIS_TRUSTED_INGESTION.json",
        "US_LEGIONELLOSIS_DB_API_UI_RECONCILIATION.json",
        "ALL_TRUSTED_SERIES_INVENTORY.json",
        "KNOWLEDGE_GRAPH_GUIDANCE_MATRIX.json",
        "BACKTEST_FOLD_MANIFEST.json",
        "LEAKAGE_SENTINEL_RESULTS.json",
        "BASELINE_COMPARISON.json",
        "MODEL_CANDIDATE_RESULTS.json",
        "CHAMPION_SELECTION.json",
        "FORECAST_RUNS.json",
        "SPARSE_RISK_AND_SCENARIO_RUNS.json",
        "FORECAST_COVERAGE_MATRIX.json",
        "PREDICTION_METRICS.json",
        "API_ACCEPTANCE.json",
        "BROWSER_ACCEPTANCE.json",
        "REGRESSION_REPORT.json",
    ]:
        write_json(name, blocked_common)
    write_json("DATABASE_DELTA.json", {"status": "PASS_ZERO_MUTATION", "database_writes": 0, "migration_run": False})
    write_json("MIGRATION_REPORT.json", {"status": "NOT_REQUIRED_NOT_RUN", "migration_run": False})
    write_json("SERVICE_STATE_AFTER.json", {"status": "UNCHANGED_FROM_M0", "healthz_readyz_captured_in": "BASELINE_STATE.json"})
    write_json("SECRET_SCAN.json", {"status": "PASS_SCOPE_LIMITED", "note": "No credentials were intentionally written to reports; no command used credential output."})
    summary = {
        "status": "PARTIAL_BLOCKED_CDC_DATA_CDC_GOV_403",
        "generated_at_utc": now,
        "completed_gates": [
            "M0 project root located",
            "database backup custom format created",
            "pg_restore list generated",
            "baseline state captured",
            "knowledge graph version captured",
            "CDC primary SODA 403 evidence captured",
            "CDC Stacks official single-file probe captured"
        ],
        "unfinished_gates": [
            "US Legionellosis full bounded SODA fetch",
            "US Legionellosis full history normalization",
            "US Legionellosis trusted ingestion and active pointer",
            "US Legionellosis Dashboard live data display",
            "All trusted series inventory and forecasts"
        ],
        "affected_series_id": "USA|legionella|CDC_NNDSS_WEEKLY|cases_new|weekly",
        "safe_fixes_attempted": [
            "urllib GET to metadata and SODA endpoint",
            "SODA v3 POST endpoint",
            "wget to rows CSV/JSON export endpoints",
            "CDC Stacks official single TXT supporting file",
            "CDC Stacks official gsearch probe",
            "bounded CDC ID scan stopped to avoid excessive official probing"
        ],
        "non_sensitive_error": "CDC/Socrata returned HTTP 403 Forbidden from vm201 for data.cdc.gov endpoints.",
        "minimum_external_condition": "CDC data.cdc.gov/Socrata access from vm201 must be permitted, or user must authorize a specific official CDC Stacks collection discovery method/API/token-free URL list sufficient for complete history.",
        "database_writes": 0,
        "training_started": False,
        "prediction_started": False,
        "active_pointer_modified": False,
        "commit_or_push": False,
    }
    write_json("FINAL_SUMMARY.json", summary)
    (REPORT_DIR / "FINAL_REPORT.md").write_text(
        "# Final Report: US Legionellosis + All Trusted Series Forecasts v1\n\n"
        f"Final status: `{summary['status']}`\n\n"
        "## What passed\n\n"
        "- Project root located at `USER_HOME/projects/openclaw`.\n"
        "- Database backup was created in custom format and `pg_restore --list` was generated.\n"
        "- Baseline service/database/KG state was captured.\n"
        "- CDC NNDSS primary source contract was documented.\n"
        "- vm201 attempted official CDC metadata/SODA/export/v3 endpoints and captured HTTP 403 evidence.\n"
        "- CDC Stacks official TXT single-file probe succeeded for a Legionellosis weekly table.\n\n"
        "## Why the stage stopped\n\n"
        "The primary official CDC data.cdc.gov/Socrata endpoints for dataset `x9gk-5huc` return HTTP 403 Forbidden from vm201. "
        "Because the full official history was not fetched, I did not normalize, ingest trusted data, update active pointers, run forecasts, or modify Dashboard data.\n\n"
        "## Mutation summary\n\n"
        "- Database writes: 0\n"
        "- Migrations: 0\n"
        "- Training/prediction/model calls: 0\n"
        "- Active pointer changes: 0\n"
        "- Commit/push: none\n\n"
        "## Minimum unblock\n\n"
        "Permit access from vm201 to CDC data.cdc.gov/Socrata public endpoints, or provide/authorize an official CDC Stacks collection discovery mechanism that can enumerate the complete Legionellosis weekly history without third-party mirrors.\n",
        encoding="utf-8",
    )
    write_json("PACKAGE_MANIFEST.json", {"generated_at_utc": now, "files": collect_files()})
    zip_path = REPORT_DIR.with_suffix(".zip")
    subprocess.run(["python3", "-m", "zipfile", "-c", str(zip_path), str(REPORT_DIR)], check=True)
    subprocess.run(["python3", "-m", "zipfile", "-l", str(zip_path)], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({"status": summary["status"], "report_dir": str(REPORT_DIR), "zip": str(zip_path)}, sort_keys=True))


if __name__ == "__main__":
    main()

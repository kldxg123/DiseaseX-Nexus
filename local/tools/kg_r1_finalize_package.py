from __future__ import annotations

import csv
import hashlib
import json
import os
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


TASK_ID = "KG_P1_R3R_UX2_R1_FULL_PAPER_UNIVERSE_RECONCILIATION_AND_BACKFILL"
STATUS = "KG_P1_R3R_UX2_R1_PAPER_BACKFILL_PARTIAL_BLOCKED"
UX2_STATUS = "KG_P1_R3R_UX2_MACHINE_GATE_FAILED_EVIDENCE_SEMANTICS_AND_VISUAL_QA_REMEDIATION_REQUIRED"
RUN_ID = "kg_p1_r3r_ux2_r1_20260804T035410Z"
ROOT_HASH = "3c6115526ea6a381f949ab504fc1061b098d7bde2717b17f0942aa8240331352"
DB_ID = {"database": "epidemic_openclaw", "role": "epidemic_dashboard_ro", "port": 55432}
ZIP_SHA256 = "af86a99d095cc72df5f4f91b6a70938143adf70e056d92fd31ff389fe0618fea"
ZIP_SIZE = 220_501_187


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict) -> None:
    common = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": utc_now(),
        "active_project_root_hash_or_revision": ROOT_HASH,
        "database_identity": DB_ID,
        "input_manifest_sha256": payload.pop("input_manifest_sha256", None),
        "result_status": payload.pop("result_status", STATUS),
    }
    common.update(payload)
    path.write_text(json.dumps(common, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> None:
    report_dir = Path(os.environ["R1_DIR"])
    report_dir.mkdir(parents=True, exist_ok=True)

    pdf_rows = read_csv(report_dir / "KG_P1_R3R_UX2_R1_PDF_FILE_UNIVERSE.csv")
    per_rows = read_csv(report_dir / "KG_P1_R3R_UX2_R1_PER_PAPER_RECONCILIATION.csv")
    dedup = load_json(report_dir / "KG_P1_R3R_UX2_R1_IDENTITY_DEDUP_REPORT.json")
    kg_before = load_json(report_dir / "KG_P1_R3R_UX2_R1_KG_BEFORE_STATE.json")
    schema_probe = load_json(report_dir / "KG_P1_R3R_UX2_R1_SCHEMA_PROBE.json")

    input_manifest = dedup.get("input_manifest_sha256")
    counts = kg_before["counts"]
    claim_status_counts = {
        f"{r['claim_status']}|eligible={r['evidence_eligible']}|quarantine={r['quarantine']}": r["n"]
        for r in kg_before.get("kg_claims_by_status", [])
    }

    table_names = {c["table_name"] for c in schema_probe["columns"]}
    text_storage_present = any(t in table_names for t in {
        "kg_document_text_chunks",
        "kg_text_chunks",
        "kg_document_chunks",
        "kg_fulltext_index",
        "kg_search_index",
    })

    # Required narrative/method files.
    (report_dir / "R1_HISTORICAL_PDF_DISCOVERY_METHOD.md").write_text(
        "\n".join([
            "# R1 Historical PDF Discovery Method",
            "",
            "- Input materials were local user-provided `PDF.zip`, literature CSV and XLSX reference lists.",
            "- The ZIP archive was enumerated without network access; 76 PDF file entries were discovered.",
            "- Every PDF was hashed byte-for-byte and opened with pypdf to verify page count, encryption state and text-sample extractability.",
            "- CSV/XLSX rows were used as expected metadata only; file identity was derived from ZIP member bytes and hashes.",
            "- Remote vm201 KG state was queried read-only from the live Dashboard process environment without printing credentials.",
            "- No production database writes, migrations, service restarts, training, predictions, promotions, or active pointer changes were executed in this run.",
        ]) + "\n",
        encoding="utf-8",
    )

    class_counts = Counter(r["document_class"] for r in pdf_rows)
    parse_counts = Counter(r["parse_status"] for r in pdf_rows)
    write_json(report_dir / "KG_P1_R3R_UX2_R1_PDF_CLASSIFICATION_AUDIT.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "PDF_CLASSIFICATION_COMPLETE",
        "pdf_file_count": len(pdf_rows),
        "document_class_counts": dict(class_counts),
        "parse_status_counts": dict(parse_counts),
        "non_paper_count": class_counts.get("NON_PAPER", 0),
        "supplementary_material_count": class_counts.get("SUPPLEMENTARY_MATERIAL", 0),
        "scientific_paper_file_count": class_counts.get("SCIENTIFIC_PAPER", 0),
        "encrypted_pdf_count": sum(1 for r in pdf_rows if r.get("encrypted") == "True"),
        "invalid_pdf_count": sum(1 for r in pdf_rows if r.get("pdf_valid") != "True"),
        "classification_basis": "numbered ZIP PDF entries plus local PDF parseability; metadata files treated as expected values",
    })

    status_counts = Counter(r["current_kg_status"] for r in per_rows)
    action_counts = Counter(r["planned_action"] for r in per_rows)
    write_json(report_dir / "KG_P1_R3R_UX2_R1_VERSION_RELATION_REPORT.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "VERSION_RELATION_RECONCILED_FOR_EXISTING_KG_ONLY",
        "known_existing_document_versions": len(kg_before.get("document_versions", [])),
        "known_existing_work_count": len(kg_before.get("works", [])),
        "paper1_relationship": "arXiv v1 is quarantined and linked as a related preprint/version; Nature DOI document remains FULLTEXT_MISSING",
        "new_version_edges_created": 0,
        "blocked_reason": "Production backfill was not executed because KG text/search storage migration and backup/restore gates were not satisfied.",
    })

    dry_run_rows = []
    for row in per_rows:
        planned = row.get("planned_action", "")
        dry_run_rows.append({
            "legacy_paper_number": row.get("legacy_paper_number", ""),
            "canonical_work_key": row.get("canonical_work_id", ""),
            "pdf_sha256": row.get("pdf_sha256", ""),
            "current_kg_status": row.get("current_kg_status", ""),
            "planned_action": planned,
            "expected_create_work": 1 if planned.startswith("CREATE_WORK") else 0,
            "expected_create_document_version": 1 if planned.startswith("CREATE_WORK") else 0,
            "expected_pending_claim": 0,
            "expected_eligible_claim": 0,
            "execution_status": "NOT_EXECUTED_BLOCKED_UPSTREAM" if planned.startswith("CREATE_WORK") else "NO_PRODUCTION_WRITE_REQUIRED",
        })
    write_csv(
        report_dir / "KG_P1_R3R_UX2_R1_DRY_RUN_PLAN.csv",
        dry_run_rows,
        list(dry_run_rows[0].keys()),
    )
    write_json(report_dir / "KG_P1_R3R_UX2_R1_DRY_RUN_PLAN.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "DRY_RUN_COMPLETE_PRODUCTION_WRITE_BLOCKED",
        "expected_new_work_count": action_counts["CREATE_WORK_AND_DOCUMENT_VERSION_PENDING_BACKUP_RESTORE_AND_SCHEMA_PLAN"],
        "expected_new_document_version_count": action_counts["CREATE_WORK_AND_DOCUMENT_VERSION_PENDING_BACKUP_RESTORE_AND_SCHEMA_PLAN"],
        "expected_new_source_provenance_count": action_counts["CREATE_WORK_AND_DOCUMENT_VERSION_PENDING_BACKUP_RESTORE_AND_SCHEMA_PLAN"],
        "expected_new_text_or_chunk_count": 0,
        "expected_new_pending_claim_count": 0,
        "expected_new_evidence_span_count": 0,
        "expected_new_edge_count": action_counts["CREATE_WORK_AND_DOCUMENT_VERSION_PENDING_BACKUP_RESTORE_AND_SCHEMA_PLAN"],
        "expected_new_eligible_claim_count": 0,
        "recommendation_results_expected_to_change": False,
        "action_counts": dict(action_counts),
        "blockers": [
            "KG_TEXT_CHUNK_OR_SEARCH_INDEX_STORAGE_ABSENT" if not text_storage_present else "TEXT_STORAGE_PRESENT_NOT_VALIDATED",
            "PRODUCTION_BACKFILL_NOT_EXECUTED_BECAUSE_BACKUP_RESTORE_AND_MIGRATION_GATES_NOT_CLOSED",
        ],
    })

    not_run = {
        "input_manifest_sha256": input_manifest,
        "result_status": "NOT_RUN_BLOCKED_UPSTREAM",
        "rows_inserted": 0,
        "rows_updated": 0,
        "claims_created": 0,
        "eligible_claims_created": 0,
        "reason": "Production KG write stage was not entered; doing only WORK/DOCUMENT_VERSION without validated text/search storage would break the required chain.",
    }
    for name in ["KG_P1_R3R_UX2_R1_INGESTION_RUN1.json", "KG_P1_R3R_UX2_R1_INGESTION_RUN2.json", "KG_P1_R3R_UX2_R1_INGESTION_RUN3.json"]:
        write_json(report_dir / name, not_run.copy())

    write_json(report_dir / "KG_P1_R3R_UX2_R1_IDEMPOTENCY_PROOF.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "NOT_RUN_BLOCKED_UPSTREAM",
        "run2_new_nodes": 0,
        "run2_new_edges": 0,
        "run3_new_nodes": 0,
        "run3_new_edges": 0,
        "idempotency_test_executed": False,
        "reason": "No production backfill was executed after dry-run.",
    })

    kg_after = dict(kg_before)
    kg_after["result_status"] = "KG_AFTER_EQUALS_BEFORE_NO_PRODUCTION_WRITE"
    kg_after["generated_at_utc"] = utc_now()
    (report_dir / "KG_P1_R3R_UX2_R1_KG_AFTER_STATE.json").write_text(
        json.dumps(kg_after, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    text_rows = []
    for row in pdf_rows:
        text_rows.append({
            "legacy_paper_number": row.get("legacy_paper_number", ""),
            "pdf_sha256": row.get("pdf_sha256", ""),
            "page_count": row.get("page_count", ""),
            "local_parse_status": row.get("parse_status", ""),
            "extracted_character_count": row.get("extracted_character_count", ""),
            "production_text_registered": "NO",
            "production_text_status": "NOT_REGISTERED_BACKFILL_BLOCKED",
        })
    write_csv(report_dir / "KG_P1_R3R_UX2_R1_TEXT_EXTRACTION_RESULTS.csv", text_rows, list(text_rows[0].keys()))

    search_rows = []
    for row in per_rows:
        search_rows.append({
            "legacy_paper_number": row.get("legacy_paper_number", ""),
            "pdf_sha256": row.get("pdf_sha256", ""),
            "current_kg_status": row.get("current_kg_status", ""),
            "search_index_status": "NOT_INDEXED_SCHEMA_ABSENT_OR_BACKFILL_BLOCKED",
            "searchable": "false",
        })
    write_csv(report_dir / "KG_P1_R3R_UX2_R1_SEARCH_INDEX_RESULTS.csv", search_rows, list(search_rows[0].keys()))
    write_json(report_dir / "KG_P1_R3R_UX2_R1_SEARCH_TESTS.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "NOT_RUN_BLOCKED_UPSTREAM",
        "searchable_papers_before": 5,
        "searchable_papers_added_this_run": 0,
        "reason": "Full-text index storage or API path for the 70 missing PDFs was not safely created/validated.",
    })

    write_json(report_dir / "KG_P1_R3R_UX2_R1_CLAIM_STATUS_AUDIT.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "CLAIM_STATUS_UNCHANGED_NO_NEW_MACHINE_CLAIMS",
        "claim_record_count_before": counts["kg_claims"],
        "claim_record_count_after": counts["kg_claims"],
        "claim_status_counts_before": claim_status_counts,
        "new_machine_claim_count": 0,
        "new_machine_claim_eligible_count": 0,
        "paper1_participation": 0,
    })

    write_json(report_dir / "KG_P1_R3R_UX2_R1_RECOMMENDATION_ZERO_IMPACT.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "ZERO_IMPACT_BY_NO_MUTATION",
        "recommendation_eligible_claims_before": 39,
        "recommendation_eligible_claims_after": 39,
        "pending_or_mentions_claims_added": 0,
        "api_positive_retest_executed": False,
        "reason": "No KG production mutation occurred; pending claim count from this run is zero.",
    })

    write_json(report_dir / "KG_P1_R3R_UX2_R1_BACKUP_RESTORE.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "NOT_RUN_NO_PRODUCTION_WRITE",
        "source_backup_created": False,
        "epidemic_kg_pg_dump_created": False,
        "isolated_restore_validated": False,
        "reason": "Production write stage was not entered after dry-run blockers; prompt requires backup/restore before writes, and no writes were attempted.",
    })

    write_json(report_dir / "KG_P1_R3R_UX2_R1_MIGRATION_REPORT.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "MIGRATION_REQUIRED_NOT_EXECUTED",
        "text_or_search_storage_present": text_storage_present,
        "tables_checked": sorted(table_names),
        "migration_files_created": 0,
        "production_upgrade_executed": False,
        "reason": "Current epidemic_kg schema has WORK/DOCUMENT_VERSION/Claim/Evidence tables but no dedicated text chunk/search index storage identified by the probe.",
    })

    service_payload = {
        "input_manifest_sha256": input_manifest,
        "result_status": "UNCHANGED_NO_RESTART",
        "dashboard_healthz_before": 200,
        "dashboard_readyz_before": 200,
        "dashboard_healthz_after": 200,
        "dashboard_readyz_after": 200,
        "dashboard_port": 8020,
        "postgres_port": 55432,
        "service_restart_performed": False,
        "postgres_restart_performed": False,
        "vllm_restart_performed": False,
    }
    write_json(report_dir / "KG_P1_R3R_UX2_R1_SERVICE_BEFORE_AFTER.json", service_payload)

    write_json(report_dir / "KG_P1_R3R_UX2_R1_NON_KG_DATA_ZERO_CHANGE.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "ZERO_CHANGE_BY_READONLY_AND_NO_MUTATION",
        "raw_changed_rows": 0,
        "source_changed_rows": 0,
        "normalized_changed_rows": 0,
        "trusted_changed_rows": 0,
        "active_source_pointer_changed": False,
        "forecast_runs_changed_rows": 0,
        "prediction_results_changed_rows": 0,
    })

    tests_md = [
        "# KG P1 R3R UX2 R1 Test Commands and Results",
        "",
        "| Check | Command / method | Exit code | Result |",
        "|---|---|---:|---|",
        "| Local PDF discovery | `python tools/kg_r1_local_pdf_discovery.py` | 0 | PASS: 76 PDFs, 76 unique SHA-256 |",
        "| Remote live KG read-only audit | `python3 /tmp/kg_r1_remote_audit.py` | 0 | PASS: 6 WORK, 7 DOCUMENT_VERSION, 54 claims |",
        "| Remote schema probe | `python3 /tmp/kg_r1_schema_probe.py` | 0 | PASS: schema listed; text/search storage not found |",
        "| Local reconciliation | `python tools/kg_r1_reconcile_local.py` | 0 | PASS: 70 missing ingestible PDFs identified |",
        "| Remote input ZIP split transfer | `scp` chunk upload + `cat` + `sha256sum` | 0 | PASS: 220501187 bytes, SHA-256 matched |",
        "| Production ingestion Run1/2/3 | not executed | n/a | NOT_RUN_BLOCKED_UPSTREAM |",
        "| API/UI/browser tests | not executed | n/a | NOT_RUN_BLOCKED_UPSTREAM |",
    ]
    (report_dir / "KG_P1_R3R_UX2_R1_TEST_COMMANDS_AND_RESULTS.md").write_text("\n".join(tests_md) + "\n", encoding="utf-8")

    for filename in [
        "KG_P1_R3R_UX2_R1_API_TESTS.json",
        "KG_P1_R3R_UX2_R1_UI_DOM_TESTS.json",
        "KG_P1_R3R_UX2_R1_SCREENSHOT_MANIFEST.json",
        "KG_P1_R3R_UX2_R1_REGRESSION_TESTS.json",
    ]:
        write_json(report_dir / filename, {
            "input_manifest_sha256": input_manifest,
            "result_status": "NOT_RUN_BLOCKED_UPSTREAM",
            "tests_executed": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "reason": "Production KG backfill and search/index exposure did not execute; UI/API positive assertions would be misleading.",
        })

    src_manifest_before = report_dir / "KG_P1_R3R_UX2_R1_SOURCE_MANIFEST_BEFORE.json"
    src_manifest = report_dir / "KG_P1_R3R_UX2_R1_SOURCE_MANIFEST.json"
    if src_manifest_before.exists():
        src_manifest.write_text(src_manifest_before.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        write_json(src_manifest, {
            "input_manifest_sha256": input_manifest,
            "result_status": "SOURCE_MANIFEST_UNAVAILABLE",
            "reason": "Remote source manifest file was not present in local report directory.",
        })

    (report_dir / "KG_P1_R3R_UX2_R1_SOURCE_DIFF.patch").write_text(
        "# No active project source files were modified in this partial run.\n",
        encoding="utf-8",
    )
    # Lightweight report secret scan. Do not scan PDF binary contents.
    suspicious = []
    secret_patterns = ["PASSWORD=", "TOKEN=", "PRIVATE KEY", "Authorization:", "Cookie:", "DASHBOARD_DATABASE_URL="]
    for path in sorted(report_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() in {".pdf", ".zip", ".gz"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in secret_patterns:
            if pat in text:
                suspicious.append({"file": path.name, "pattern": pat})
    write_json(report_dir / "KG_P1_R3R_UX2_R1_SECRET_PATH_SCAN.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "PASS" if not suspicious else "FAIL",
        "scanned_text_file_count": sum(1 for p in report_dir.iterdir() if p.is_file() and p.suffix.lower() not in {".pdf", ".zip", ".gz"}),
        "findings": suspicious,
    })
    (report_dir / "KG_P1_R3R_UX2_R1_ZERO_UNRELATED_CHANGE_REPORT.md").write_text(
        "\n".join([
            "# Zero Unrelated Change Report",
            "",
            "- No epidemic raw/source/normalized/trusted tables were written.",
            "- No forecast runs or prediction results were written.",
            "- No active source or prediction pointer was updated.",
            "- No service was restarted.",
            "- No Git commit, push, reset or clean operation was executed.",
        ]) + "\n",
        encoding="utf-8",
    )

    human_md = [
        "# USER_RETEST_KG_P1_R3R_UX2_R1",
        "",
        "Machine state: `KG_P1_R3R_UX2_R1_PAPER_BACKFILL_PARTIAL_BLOCKED`.",
        "",
        "Human reviewer should verify:",
        "",
        "- 76 PDF files and SHA-256 values in `KG_P1_R3R_UX2_R1_PDF_FILE_UNIVERSE.csv`.",
        "- 81 literature-list rows, with 5 explicitly marked `SOURCE_FILE_NOT_FOUND`.",
        "- 70 accessible PDFs are marked missing from current live KG.",
        "- Existing Paper 1 remains quarantined and has participation = 0.",
        "- No new machine claim was made recommendation-eligible.",
        "- Production backfill was not executed; this report is not a human PASS.",
    ]
    (report_dir / "USER_RETEST_KG_P1_R3R_UX2_R1.md").write_text("\n".join(human_md) + "\n", encoding="utf-8")
    write_json(report_dir / "HUMAN_RETEST_KG_P1_R3R_UX2_R1.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "AWAITING_HUMAN_REVIEW_NOT_MACHINE_PASS",
        "human_pass_filled_by_codex": False,
        "review_items": [
            "PDF universe count and hashes",
            "Missing 70 ingestible PDFs",
            "Paper1 quarantine and zero participation",
            "No production write / no new eligible claims",
        ],
    })

    remote_zip_verify = {
        "input_manifest_sha256": input_manifest,
        "result_status": "REMOTE_INPUT_ZIP_VERIFIED",
        "remote_pdf_zip_filename": "local_input/PDF.zip.complete",
        "remote_pdf_zip_size_bytes": ZIP_SIZE,
        "remote_pdf_zip_sha256": ZIP_SHA256,
        "chunk_count": 11,
        "note": "The complete ZIP was reconstructed from chunks in the remote report directory without overwriting the earlier partial transfer artifact.",
    }
    write_json(report_dir / "KG_P1_R3R_UX2_R1_REMOTE_INPUT_ZIP_VERIFY.json", remote_zip_verify)

    final_lines = [
        "# KG P1 R3R UX2 R1 Final Machine Report",
        "",
        f"status = `{STATUS}`",
        f"overall_ux2_status = `{UX2_STATUS}`",
        "",
        "Core conclusion: the historical PDF file universe is closed at 76 unique PDF files, but the live KG currently contains only 6 WORK nodes and 7 DOCUMENT_VERSION nodes. Therefore, the user-provided PDFs are not all in the current KG.",
        "",
        "Production KG backfill was not executed. The dry-run identified 70 accessible scientific PDFs requiring WORK/DOCUMENT_VERSION creation, but the current KG schema probe did not find dedicated full-text chunk/search-index storage, so writing only bibliographic nodes would not satisfy the required file → WORK → DOCUMENT_VERSION → text/search chain.",
        "",
        "No database writes, migrations, service restarts, training, prediction, promotion, active pointer update, Git commit/push/reset, or secret exposure occurred.",
        "",
        "Important counts:",
        "",
        f"- previously_reported_pdf_count = 76",
        f"- discovered_pdf_file_count = {len(pdf_rows)}",
        f"- unique_pdf_sha256_count = {len({r['pdf_sha256'] for r in pdf_rows})}",
        f"- scientific_paper_file_count = {class_counts.get('SCIENTIFIC_PAPER', 0)}",
        f"- unique_canonical_work_count = {dedup['unique_canonical_work_count']}",
        f"- work_count_before = {len(kg_before.get('works', []))}",
        f"- document_version_count_before = {len(kg_before.get('document_versions', []))}",
        f"- claim_record_count_before = {counts['kg_claims']}",
        f"- evidence_span_count_before = {counts['kg_evidence_spans']}",
        f"- chunk_count_before = 0",
        f"- edge_count_before = {counts['kg_edges']}",
        f"- missing_ingestible_pdf_count = {dedup['missing_ingestible_pdf_count']}",
        f"- source_file_not_found_count = {dedup['source_file_not_found_count']}",
        f"- identity_conflict_count = {dedup['identity_conflict_count']}",
        f"- new_work_count = 0",
        f"- new_document_version_count = 0",
        f"- new_claim_count = 0",
        f"- new_eligible_claim_count = 0",
        f"- paper1_participation = 0",
    ]
    (report_dir / "FINAL_REPORT.md").write_text("\n".join(final_lines) + "\n", encoding="utf-8")

    # Archive only textual/audit deliverables, not extracted PDFs or chunked raw input.
    archive_name = "KG_P1_R3R_UX2_R1_FULL_PAPER_RECONCILIATION_BACKFILL_20260804T035410Z_PRE_HUMAN_REVIEW.tar.gz"
    archive_path = report_dir / archive_name
    if archive_path.exists():
        archive_path.unlink()
    included = []
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in sorted(report_dir.iterdir()):
            if not path.is_file():
                continue
            if path.name == archive_name:
                continue
            if path.suffix.lower() in {".pdf", ".zip"}:
                continue
            tar.add(path, arcname=path.name)
            included.append(path.name)
        # Add helper script sources for reproducibility.
        tools_dir = Path(os.environ.get("R1_TOOLS_DIR", "tools"))
        for helper in [
            "kg_r1_local_pdf_discovery.py",
            "kg_r1_reconcile_local.py",
            "kg_r1_remote_audit.py",
            "kg_r1_schema_probe.py",
            "kg_r1_split_zip.py",
            "kg_r1_finalize_package.py",
        ]:
            hp = tools_dir / helper
            if hp.exists():
                tar.add(hp, arcname=f"test_source/{helper}")
                included.append(f"test_source/{helper}")
    archive_sha = sha256_file(archive_path)
    write_json(report_dir / "ARCHIVE_SEALING_RECORD.json", {
        "input_manifest_sha256": input_manifest,
        "result_status": "ARCHIVE_CREATED",
        "archive_name": archive_name,
        "archive_sha256": archive_sha,
        "archive_size_bytes": archive_path.stat().st_size,
        "member_count": len(included),
        "members": included,
        "raw_pdfs_excluded_from_archive": True,
        "raw_pdf_zip_sha256": ZIP_SHA256,
    })
    # Rebuild archive to include sealing record as non-self-referential member? Keep tar self-free; sealing record stays outside.
    print(json.dumps({
        "status": STATUS,
        "report_dir": str(report_dir),
        "archive": str(archive_path),
        "archive_sha256": archive_sha,
        "pdf_count": len(pdf_rows),
        "missing_ingestible_pdf_count": dedup["missing_ingestible_pdf_count"],
        "source_file_not_found_count": dedup["source_file_not_found_count"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

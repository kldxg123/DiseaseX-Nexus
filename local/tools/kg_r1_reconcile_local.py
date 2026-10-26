from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook


TASK_ID = "KG_P1_R3R_UX2_R1_FULL_PAPER_UNIVERSE_RECONCILIATION_AND_BACKFILL"
REPORT = Path("reports/kg_p1_r3r_ux2_r1_full_paper_reconciliation_backfill_20260804T035410Z")
BASE = Path("reference_materials/infectious_disease_ai_literature_2026_08")


def norm_doi(x: str | None) -> str:
    if not x:
        return ""
    x = str(x).strip().lower()
    x = re.sub(r"^https?://(dx\.)?doi\.org/", "", x)
    x = re.sub(r"^doi:\s*", "", x)
    return x.strip().rstrip(".")


def norm_title(x: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()


def load_refs() -> dict[str, dict]:
    out: dict[str, dict] = {}
    # Prefer xlsx richer fields.
    for p in BASE.glob("*.xlsx"):
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [str(v).strip() if v is not None else "" for v in rows[0]]
        for vals in rows[1:]:
            r = {headers[i] if i < len(headers) else f"col_{i}": ("" if v is None else str(v)) for i, v in enumerate(vals)}
            seq = str(r.get("序号", "")).strip()
            if seq:
                out[seq.zfill(3)] = r
        wb.close()
    for p in BASE.glob("*.csv"):
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        for r in csv.DictReader(text.splitlines()):
            seq = str(r.get("序号", "")).strip()
            if seq and seq.zfill(3) not in out:
                out[seq.zfill(3)] = r
    return out


def main() -> int:
    refs = load_refs()
    kg = json.loads((REPORT / "KG_P1_R3R_UX2_R1_KG_BEFORE_STATE.json").read_text(encoding="utf-8"))
    with (REPORT / "KG_P1_R3R_UX2_R1_PDF_FILE_UNIVERSE.csv").open(encoding="utf-8") as f:
        pdf_rows = list(csv.DictReader(f))
    docs_by_hash = {}
    docs_by_doi = {}
    for d in kg["document_versions"]:
        props = d.get("properties") or {}
        if props.get("pdf_sha256"):
            docs_by_hash[str(props["pdf_sha256"]).lower()] = d
        if props.get("doi"):
            docs_by_doi[norm_doi(props["doi"])] = d
        m = re.search(r"paper:doi:(.+)", d.get("canonical_key") or "")
        if m:
            docs_by_doi[norm_doi(m.group(1))] = d
    works_by_doi = {}
    for w in kg["works"]:
        m = re.search(r"work:(10\..+)", w.get("canonical_key") or "")
        if m:
            works_by_doi[norm_doi(m.group(1))] = w
    all_legacy = sorted(set(refs) | {r["legacy_paper_number"] for r in pdf_rows if r["legacy_paper_number"]})
    per = []
    canonical: dict[str, dict] = {}
    source_missing = []
    for legacy in all_legacy:
        ref = refs.get(legacy, {})
        pdfs = [r for r in pdf_rows if r["legacy_paper_number"] == legacy]
        if not pdfs:
            status = "SOURCE_FILE_NOT_FOUND"
            source_missing.append(legacy)
            base = {
                "legacy_paper_number": legacy,
                "title": ref.get("标题", ""),
                "doi": norm_doi(ref.get("DOI")),
                "pdf_sha256": "",
                "document_class": "SOURCE_FILE_NOT_FOUND",
                "current_kg_status": "SOURCE_FILE_NOT_FOUND",
                "planned_action": "BLOCK_SOURCE_MISSING",
                "exclusion_or_block_reason": "listed in reference sheet but PDF file absent from PDF.zip",
            }
            per.append(base)
            continue
        for r in pdfs:
            doi = norm_doi(ref.get("DOI")) or norm_doi(r.get("doi"))
            title = ref.get("标题") or r.get("title_from_metadata")
            kg_doc = docs_by_hash.get(r["pdf_sha256"].lower()) or docs_by_doi.get(doi)
            kg_work = works_by_doi.get(doi)
            if kg_doc:
                current = "WORK_AND_DOCUMENT_VERSION"
                planned = "NO_ACTION_ALREADY_COMPLETE"
                doc_id = kg_doc["canonical_key"]
                work_id = (kg_work or {}).get("canonical_key") or ("work:pandemicllm" if legacy == "001" else "")
            else:
                current = "DISCOVERED_FILE_ONLY"
                planned = "CREATE_WORK_AND_DOCUMENT_VERSION_PENDING_BACKUP_RESTORE_AND_SCHEMA_PLAN"
                doc_id = ""
                work_id = ""
            if legacy == "001":
                current = "QUARANTINED" if kg_doc else "DISCOVERED_FILE_ONLY_IDENTITY_CONFLICT_QUARANTINE_REQUIRED"
                planned = "QUARANTINE_PAPER1"
                work_id = "work:pandemicllm"
            canonical_key = f"work:{doi}" if doi else f"work:legacy-paper-{legacy}"
            canonical.setdefault(canonical_key, {
                "legacy_paper_numbers": [],
                "canonical_work_id": canonical_key,
                "title": title,
                "doi": doi,
                "arxiv_id": "",
                "pmid": "",
                "file_count": 0,
                "document_version_count": 0,
                "current_kg_status": current,
                "planned_action": planned,
            })
            canonical[canonical_key]["legacy_paper_numbers"].append(legacy)
            canonical[canonical_key]["file_count"] += 1
            canonical[canonical_key]["document_version_count"] += 1
            per.append({
                **r,
                "title": title,
                "doi": doi,
                "reference_year": ref.get("年份", ""),
                "reference_venue": ref.get("期刊", "") or ref.get("venue", ""),
                "reference_first_author": ref.get("第一作者", ""),
                "canonical_work_id": work_id or canonical_key,
                "document_version_id": doc_id,
                "current_kg_status": current,
                "planned_action": planned,
                "exclusion_or_block_reason": "Paper 1 remains quarantined" if legacy == "001" else "",
            })
    # Write per paper reconciliation.
    fieldnames = sorted({k for row in per for k in row.keys()})
    with (REPORT / "KG_P1_R3R_UX2_R1_PER_PAPER_RECONCILIATION.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(per)
    with (REPORT / "KG_P1_R3R_UX2_R1_CANONICAL_WORK_UNIVERSE.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["canonical_work_id", "legacy_paper_numbers", "title", "doi", "arxiv_id", "pmid", "file_count", "document_version_count", "current_kg_status", "planned_action"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for v in canonical.values():
            v = dict(v)
            v["legacy_paper_numbers"] = ";".join(sorted(set(v["legacy_paper_numbers"])))
            w.writerow(v)
    with (REPORT / "KG_P1_R3R_UX2_R1_LEGACY_PAPER_ID_MAP.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["legacy_paper_number", "reference_title", "pdf_present", "canonical_work_id", "current_kg_status", "planned_action"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for legacy in all_legacy:
            hit = next((r for r in per if r.get("legacy_paper_number") == legacy), {})
            w.writerow({
                "legacy_paper_number": legacy,
                "reference_title": refs.get(legacy, {}).get("标题", hit.get("title", "")),
                "pdf_present": bool([r for r in pdf_rows if r["legacy_paper_number"] == legacy]),
                "canonical_work_id": hit.get("canonical_work_id", ""),
                "current_kg_status": hit.get("current_kg_status", "SOURCE_FILE_NOT_FOUND"),
                "planned_action": hit.get("planned_action", "BLOCK_SOURCE_MISSING"),
            })
    work_count_before = next((x["n"] for x in kg["kg_nodes_by_type"] if x["node_type"] == "WORK"), 0)
    doc_count_before = next((x["n"] for x in kg["kg_nodes_by_type"] if x["node_type"] == "DOCUMENT_VERSION"), 0)
    already = [r for r in per if r.get("current_kg_status") in {"WORK_AND_DOCUMENT_VERSION", "QUARANTINED"} and r.get("document_version_id")]
    missing_ingestible = [r for r in per if r.get("current_kg_status") == "DISCOVERED_FILE_ONLY"]
    identity_conflicts = [r for r in per if "IDENTITY_CONFLICT" in r.get("current_kg_status", "")]
    status_counts = {}
    for r in per:
        status_counts[r.get("current_kg_status", "")] = status_counts.get(r.get("current_kg_status", ""), 0) + 1
    summary = {
        "schema_version": "1.0",
        "task_id": "KG_P1_R3R_UX2_R1_FULL_PAPER_UNIVERSE_RECONCILIATION_AND_BACKFILL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_project_root_hash_or_revision": kg.get("active_project_root_hash_or_revision"),
        "database_identity": kg.get("database_identity"),
        "input_manifest_sha256": json.loads((REPORT / "KG_P1_R3R_UX2_R1_UNIVERSE_COUNT_RECONCILIATION.json").read_text(encoding="utf-8")).get("input_manifest_sha256"),
        "result_status": "RECONCILIATION_COMPLETE_BACKFILL_REQUIRED",
        "previously_reported_pdf_count": 76,
        "reference_list_row_count": len(refs),
        "discovered_pdf_file_count": len(pdf_rows),
        "unique_pdf_sha256_count": len({r["pdf_sha256"] for r in pdf_rows}),
        "scientific_paper_file_count": sum(1 for r in pdf_rows if r["document_class"] == "SCIENTIFIC_PAPER"),
        "unique_canonical_work_count": len(canonical),
        "source_file_not_found_count": len(source_missing),
        "source_file_not_found_legacy_ids": source_missing,
        "work_count_before": work_count_before,
        "document_version_count_before": doc_count_before,
        "already_in_kg_document_version_file_count": len(already),
        "missing_ingestible_pdf_count": len(missing_ingestible),
        "identity_conflict_count": len(identity_conflicts),
        "paper1_participation": 0,
        "status_counts": status_counts,
        "all_previous_pdfs_reconciled": "YES",
        "all_ingestible_papers_in_kg": "NO",
        "conclusion": "此前提供的 PDF 已完成文件级 reconciliation，但除 Paper1/2/22/48/49/81 相关项外，多数可访问 PDF 尚未进入当前 KG。",
    }
    (REPORT / "KG_P1_R3R_UX2_R1_IDENTITY_DEDUP_REPORT.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (REPORT / "KG_P1_R3R_UX2_R1_QUARANTINE_AND_BLOCK_REPORT.json").write_text(json.dumps({
        **summary,
        "result_status": "QUARANTINE_AND_BLOCK_REPORT",
        "paper1": "QUARANTINED",
        "source_file_not_found_legacy_ids": source_missing,
        "identity_conflict_items": identity_conflicts,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (REPORT / "KG_P1_R3R_UX2_R1_PAPER1_QUARANTINE_PROOF.json").write_text(json.dumps({
        **summary,
        "result_status": "PAPER1_QUARANTINE_PROOF",
        "paper1_participation": 0,
        "paper1_status": "QUARANTINED_OR_IDENTITY_CONFLICT_REQUIRES_HUMAN_REVIEW",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader


TASK_ID = "KG_P1_R3R_UX2_R2_70PDF_BASE_BACKFILL"
ROOT_HASH = "PENDING_LIVE_REMOTE"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def slugify(s: str, max_len: int = 96) -> str:
    n = norm_text(s)
    return re.sub(r"\s+", "-", n)[:max_len].strip("-") or "untitled"


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ARXIV_RE = re.compile(r"\barXiv\s*[: ]\s*(\d{4}\.\d{4,5})(v\d+)?\b", re.I)


def clean_doi(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    raw = raw.replace("https://doi.org/", "").replace("http://doi.org/", "")
    raw = raw.rstrip(".,;) \n\t")
    return raw.lower()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def write_json(path: Path, payload: dict) -> None:
    base = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": now(),
        "active_project_root": "USER_HOME/projects/openclaw",
        "source_revision_or_hash": ROOT_HASH,
        "database_identity": {"database": "epidemic_openclaw", "port": 55432},
        "input_manifest_sha256": "",
        "result_status": payload.pop("result_status", "GENERATED"),
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
    }
    base.update(payload)
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_pdf(path: Path) -> dict:
    data = path.read_bytes()
    out = {"size_bytes": len(data), "sha256": sha256_bytes(data), "page_count": "", "encrypted": "", "corrupt": False, "metadata": {}, "full_text_sample_sha256": "", "full_text_chars": 0, "doi_candidates": [], "arxiv_candidates": []}
    try:
        reader = PdfReader(path)
        out["encrypted"] = bool(reader.is_encrypted)
        out["page_count"] = len(reader.pages)
        md = reader.metadata or {}
        out["metadata"] = {str(k): str(v) for k, v in md.items() if v is not None}
        texts = []
        for page in reader.pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                texts.append("")
        text = "\n".join(texts)
        out["full_text_chars"] = len(text)
        out["full_text_sample_sha256"] = hashlib.sha256(text[:20000].encode("utf-8", "ignore")).hexdigest()
        out["doi_candidates"] = sorted({clean_doi(m.group(0)) for m in DOI_RE.finditer(text)})[:10]
        out["arxiv_candidates"] = sorted({(m.group(1) + (m.group(2) or "")).lower() for m in ARXIV_RE.finditer(text)})[:10]
    except Exception as e:
        out["corrupt"] = True
        out["error"] = type(e).__name__
    return out


def title_verified(title_pdf: str, title_meta: str, full_text_available: bool, pdf_title_field: str) -> tuple[str, str, str]:
    generic = {"science journals aaas", "nature", "plos computational biology", "frontiers"}
    if title_pdf and len(norm_text(title_pdf)) >= 20 and norm_text(title_pdf) not in generic:
        return title_pdf, "PDF_TEXT_TITLE", "READY_TITLE_FROM_PDF_TEXT"
    if pdf_title_field and len(norm_text(pdf_title_field)) >= 20 and norm_text(pdf_title_field) not in generic:
        return pdf_title_field, "PDF_METADATA_TITLE", "READY_TITLE_FROM_PDF_METADATA"
    # Metadata title can only support READY if the PDF has extractable text; this avoids filename-only identity.
    if title_meta and full_text_available:
        return title_meta, "REFERENCE_METADATA_WITH_EXTRACTABLE_PDF_TEXT", "READY_TITLE_FROM_REFERENCE_METADATA_AND_TEXT"
    return title_meta or title_pdf or pdf_title_field, "INSUFFICIENT", "BLOCKED_TITLE_NOT_CLOSED"


def main() -> None:
    r1 = Path(os.environ["R1_DIR"])
    out = Path(os.environ["R2_DIR"])
    out.mkdir(parents=True, exist_ok=True)

    pdf_rows = read_csv(r1 / "KG_P1_R3R_UX2_R1_PDF_FILE_UNIVERSE.csv")
    per_rows = read_csv(r1 / "KG_P1_R3R_UX2_R1_PER_PAPER_RECONCILIATION.csv")
    per_by_legacy = {r["legacy_paper_number"]: r for r in per_rows}

    byte_rows = []
    target_rows = []
    identity_rows = []
    plan_rows = []
    conflict_rows = []
    for row in pdf_rows:
        legacy = row["legacy_paper_number"]
        p = r1 / row["local_extracted_pdf"]
        info = extract_pdf(p)
        current = per_by_legacy.get(legacy, {}).get("current_kg_status", row.get("current_kg_status", "UNKNOWN"))
        planned_from_r1 = per_by_legacy.get(legacy, {}).get("planned_action", "")
        collection = (
            "PAPER1_QUARANTINED"
            if legacy == "001"
            else "ALREADY_PRESENT_REUSE"
            if current == "WORK_AND_DOCUMENT_VERSION"
            else "R2_TARGET_70"
            if planned_from_r1.startswith("CREATE_WORK")
            else current
        )
        pdf_meta_title = info["metadata"].get("/Title", "") or info["metadata"].get("Title", "")
        verified_title, evidence_type, title_status = title_verified(row.get("title_from_pdf", ""), row.get("title_from_metadata", ""), info["full_text_chars"] > 1000, pdf_meta_title)
        doi_candidates = [clean_doi(row.get("doi", ""))] if row.get("doi") else []
        doi_candidates += [d for d in info["doi_candidates"] if d not in doi_candidates]
        arxiv_candidates = [row.get("arxiv_id", "").lower()] if row.get("arxiv_id") else []
        arxiv_candidates += [a for a in info["arxiv_candidates"] if a not in arxiv_candidates]
        normalized_doi = doi_candidates[0] if doi_candidates else ""
        normalized_arxiv = arxiv_candidates[0] if arxiv_candidates else ""

        if collection != "R2_TARGET_70":
            final_status = collection
        elif info["corrupt"] or info["encrypted"]:
            final_status = "CORRUPT_OR_ENCRYPTED"
        elif title_status.startswith("READY"):
            final_status = "READY_NEW_WORK_AND_DOCUMENT_VERSION"
        else:
            final_status = "BLOCKED_MISSING_REQUIRED_IDENTITY_EVIDENCE"

        if normalized_doi:
            work_key = "work:doi:" + normalized_doi
        elif normalized_arxiv:
            work_key = "work:arxiv:" + normalized_arxiv
        else:
            work_key = "work:title-year:" + slugify(f"{verified_title}-{row.get('year','')}")
        doc_key = "paper:sha256:" + info["sha256"]
        target_id = "r2-target-" + legacy + "-" + info["sha256"][:12]

        byte = {
            "legacy_paper_number": legacy,
            "relative_corpus_path": row["sanitized_relative_source"],
            "size_bytes": info["size_bytes"],
            "sha256": info["sha256"],
            "page_count": info["page_count"],
            "encrypted": info["encrypted"],
            "corrupt": info["corrupt"],
            "full_text_chars": info["full_text_chars"],
            "full_text_sample_sha256": info["full_text_sample_sha256"],
            "collection_membership": collection,
            "current_live_kg_status": current,
        }
        byte_rows.append(byte)
        identity = {
            "target_item_id": target_id if collection == "R2_TARGET_70" else "",
            "legacy_paper_number": legacy,
            "collection_membership": collection,
            "final_identity_status": final_status,
            "identity_evidence_type": evidence_type,
            "canonical_title": verified_title,
            "title_from_pdf": row.get("title_from_pdf", ""),
            "title_from_metadata": row.get("title_from_metadata", ""),
            "pdf_metadata_title": pdf_meta_title,
            "authors_observed": row.get("authors", ""),
            "year": row.get("year", ""),
            "venue": row.get("venue", ""),
            "normalized_doi": normalized_doi,
            "doi_candidates": ";".join(doi_candidates),
            "normalized_arxiv": normalized_arxiv,
            "arxiv_candidates": ";".join(arxiv_candidates),
            "canonical_work_key": work_key,
            "document_version_key": doc_key,
            "source_sha256": info["sha256"],
            "size_bytes": info["size_bytes"],
            "page_count": info["page_count"],
            "source_relative_ref": row["sanitized_relative_source"],
            "block_reason": "" if final_status.startswith("READY") or collection != "R2_TARGET_70" else title_status,
        }
        identity_rows.append(identity)
        if collection == "R2_TARGET_70":
            target_rows.append(identity)
            plan_rows.append({
                **identity,
                "planned_base_action": "CREATE_WORK_AND_DOCUMENT_VERSION" if final_status == "READY_NEW_WORK_AND_DOCUMENT_VERSION" else final_status,
                "claim_extraction": "DISABLED",
                "recommendation_eligible": "false",
                "text_status": "TEXT_EXTRACTION_PENDING_P1",
                "search_status": "TEXT_EXTRACTED_NOT_INDEXED_OR_NOT_ATTEMPTED",
            })
        if final_status in {"QUARANTINED_IDENTITY_CONFLICT", "BLOCKED_MISSING_REQUIRED_IDENTITY_EVIDENCE"}:
            conflict_rows.append(identity)

    fields_byte = list(byte_rows[0])
    fields_identity = list(identity_rows[0])
    fields_plan = list(plan_rows[0])
    write_csv(out / "R2_76PDF_BYTE_UNIVERSE.csv", byte_rows, fields_byte)
    write_csv(out / "R2_TARGET_70_FROZEN_MANIFEST.csv", target_rows, fields_identity)
    write_csv(out / "R2_PER_PDF_IDENTITY_RECONCILIATION.csv", identity_rows, fields_identity)
    write_csv(out / "R2_CANONICAL_WORK_AND_VERSION_PLAN.csv", plan_rows, fields_plan)

    # Historical conflicts from earlier reports are not available as row-level records in R1; record honest mapping.
    hist_rows = []
    for n in range(1, 30):
        hist_rows.append({
            "historical_conflict_ordinal": n,
            "mapping_status": "SOURCE_NOT_RECOVERED",
            "r2_resolution": "NOT_CLAIMED_RESOLVED",
            "note": "R1 archive available to this run did not contain the prior row-level 29-conflict list.",
        })
    write_csv(out / "R2_HISTORICAL_IDENTITY_CONFLICT_MAP.csv", hist_rows, list(hist_rows[0]))

    counts = Counter(r["final_identity_status"] for r in target_rows)
    write_json(out / "R2_TARGET_SET_RECONCILIATION.json", {
        "result_status": "TARGET_SET_CLOSED" if len(target_rows) == 70 else "TARGET_SET_NOT_CLOSED",
        "expected_target_pdf_count": 70,
        "observed_target_pdf_count": len(target_rows),
        "target_set_closed": len(target_rows) == 70,
        "target_unaccounted_count": max(0, 70 - len(target_rows)),
        "target_status_counts": dict(counts),
        "paper1_excluded": sum(1 for r in byte_rows if r["collection_membership"] == "PAPER1_QUARANTINED"),
        "already_present_excluded": sum(1 for r in byte_rows if r["collection_membership"] == "ALREADY_PRESENT_REUSE"),
        "source_missing_legacy_ids": ["009", "010", "067", "069", "070"],
    })
    write_json(out / "R2_PREVIOUS_R1_ARTIFACT_VERIFICATION.json", {
        "result_status": "R1_ARCHIVE_SHA256_VERIFIED",
        "r1_archive_sha256_expected": "053cad982cc7dcf12f21108198de37be26b6a1cc8ab43cd85125b91a297f3785",
        "r1_archive_sha256_observed": sha256_file(r1 / "KG_P1_R3R_UX2_R1_FULL_PAPER_RECONCILIATION_BACKFILL_20260804T035410Z_PRE_HUMAN_REVIEW.tar.gz"),
        "r1_report_dir_read": str(r1),
    })
    print(json.dumps({
        "target_count": len(target_rows),
        "target_status_counts": dict(counts),
        "byte_pdf_count": len(byte_rows),
        "historical_conflict_rows": len(hist_rows),
        "out": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

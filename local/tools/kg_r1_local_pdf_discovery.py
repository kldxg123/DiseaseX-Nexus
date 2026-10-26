from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader


TASK_ID = "KG_P1_R3R_UX2_R1_FULL_PAPER_UNIVERSE_RECONCILIATION_AND_BACKFILL"
BASE = Path("reference_materials/infectious_disease_ai_literature_2026_08")
ZIP = BASE / "PDF.zip"
OUT_ROOT = Path("reports")
UTC = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT = OUT_ROOT / f"kg_p1_r3r_ux2_r1_full_paper_reconciliation_backfill_{UTC}"
EXTRACT = OUT / "input_pdfs"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


def safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)


def legacy_number(name: str) -> str:
    m = re.search(r"(?:^|/)(\d{3})_", name)
    return m.group(1) if m else ""


def title_from_filename(name: str) -> tuple[str, str, str]:
    stem = Path(name).stem
    parts = stem.split("_", 3)
    if len(parts) >= 4:
        return parts[1], parts[2], parts[3]
    return "", "", stem


def load_csv_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for p in BASE.glob("*.csv"):
        try:
            text = p.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = p.read_text(encoding="gbk", errors="replace")
        for row in csv.DictReader(text.splitlines()):
            row["_source_file"] = p.name
            rows.append(row)
    return rows


def load_xlsx_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for p in BASE.glob("*.xlsx"):
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        data = list(ws.iter_rows(values_only=True))
        if not data:
            continue
        headers = [str(x).strip() if x is not None else "" for x in data[0]]
        for vals in data[1:]:
            row = {headers[i] if i < len(headers) else f"col_{i}": ("" if v is None else str(v)) for i, v in enumerate(vals)}
            row["_source_file"] = p.name
            rows.append(row)
        wb.close()
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    EXTRACT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_files": [],
    }
    for p in [ZIP, *BASE.glob("*.csv"), *BASE.glob("*.xlsx")]:
        manifest["input_files"].append({"name": p.name, "bytes": p.stat().st_size, "sha256": sha_file(p)})
    input_manifest_sha = sha_bytes(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode())

    csv_rows = load_csv_rows()
    xlsx_rows = load_xlsx_rows()
    (OUT / "R1_LOCAL_REFERENCE_LISTS_SUMMARY.json").write_text(json.dumps({
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_manifest_sha256": input_manifest_sha,
        "csv_row_count": len(csv_rows),
        "xlsx_row_count": len(xlsx_rows),
        "csv_columns": sorted({k for r in csv_rows for k in r.keys()})[:80],
        "xlsx_columns": sorted({k for r in xlsx_rows for k in r.keys()})[:80],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rows = []
    class_counts: dict[str, int] = {}
    sha_to_paths: dict[str, list[str]] = {}
    with zipfile.ZipFile(ZIP) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        for idx, info in enumerate(infos, 1):
            raw = info.filename
            data = z.read(info)
            pdf_sha = sha_bytes(data)
            legacy = legacy_number(raw)
            year, venue, title_file = title_from_filename(raw)
            out_pdf = EXTRACT / f"{legacy or idx:>03}_{pdf_sha[:16]}_{safe_name(raw)}"
            out_pdf.write_bytes(data)
            page_count = None
            encrypted = False
            pdf_valid = False
            parse_status = "NOT_PARSED"
            title_meta = ""
            authors = ""
            extracted_chars = 0
            try:
                reader = PdfReader(str(out_pdf))
                encrypted = bool(reader.is_encrypted)
                if encrypted:
                    parse_status = "ENCRYPTED"
                else:
                    page_count = len(reader.pages)
                    meta = reader.metadata or {}
                    title_meta = str(meta.get("/Title") or meta.get("title") or "")
                    authors = str(meta.get("/Author") or meta.get("author") or "")
                    sample = []
                    for p in reader.pages[:3]:
                        try:
                            sample.append(p.extract_text() or "")
                        except Exception:
                            pass
                    extracted_chars = sum(len(x) for x in sample)
                    pdf_valid = True
                    parse_status = "TEXT_SAMPLE_EXTRACTED" if extracted_chars else "NO_TEXT_SAMPLE_OCR_MAY_BE_REQUIRED"
            except Exception as e:
                parse_status = f"PARSE_ERROR:{type(e).__name__}"
            document_class = "SCIENTIFIC_PAPER" if raw.lower().endswith(".pdf") and re.match(r".*/?\d{3}_", raw) else "UNDETERMINED"
            if "supplement" in raw.lower() or "appendix" in raw.lower():
                document_class = "SUPPLEMENTARY_MATERIAL"
            class_counts[document_class] = class_counts.get(document_class, 0) + 1
            sha_to_paths.setdefault(pdf_sha, []).append(raw)
            rows.append({
                "source_file_id": f"local_zip:{idx:03d}",
                "legacy_paper_number": legacy,
                "raw_filename": Path(raw).name,
                "sanitized_relative_source": f"PDF.zip/{Path(raw).name}",
                "discovered_from": "reference_materials/infectious_disease_ai_literature_2026_08/PDF.zip",
                "file_size_bytes": info.file_size,
                "pdf_sha256": pdf_sha,
                "page_count": page_count if page_count is not None else "",
                "pdf_valid": pdf_valid,
                "encrypted": encrypted,
                "parse_status": parse_status,
                "ocr_required": parse_status == "NO_TEXT_SAMPLE_OCR_MAY_BE_REQUIRED",
                "extracted_character_count": extracted_chars,
                "document_class": document_class,
                "classification_evidence": "numbered PDF.zip entry; filename resembles scholarly citation",
                "title_from_pdf": title_meta,
                "title_from_metadata": title_file,
                "authors": authors,
                "year": year,
                "venue": venue,
                "doi": "",
                "arxiv_id": "",
                "pmid": "",
                "canonical_url": "",
                "identity_confidence": "FILENAME_ONLY_PENDING_REMOTE_KG_RECONCILIATION",
                "canonical_work_id": "",
                "document_version_id": "",
                "current_kg_status": "PENDING_REMOTE_KG_RECONCILIATION",
                "planned_action": "PENDING_REMOTE_KG_RECONCILIATION",
                "exclusion_or_block_reason": "",
                "local_extracted_pdf": str(out_pdf.relative_to(OUT)),
            })
    fieldnames = list(rows[0].keys()) if rows else []
    with (OUT / "KG_P1_R3R_UX2_R1_PDF_FILE_UNIVERSE.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    duplicates = {k: v for k, v in sha_to_paths.items() if len(v) > 1}
    recon = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_project_root_hash_or_revision": "PENDING_REMOTE",
        "database_identity": "PENDING_REMOTE",
        "input_manifest_sha256": input_manifest_sha,
        "result_status": "LOCAL_PDF_UNIVERSE_FROZEN_PENDING_REMOTE_RECONCILIATION",
        "previously_reported_pdf_count": 76,
        "discovered_pdf_file_count": len(rows),
        "unique_pdf_sha256_count": len(sha_to_paths),
        "scientific_paper_file_count": class_counts.get("SCIENTIFIC_PAPER", 0),
        "duplicate_file_copy_count": sum(len(v) - 1 for v in duplicates.values()),
        "duplicate_pdf_sha256_groups": duplicates,
        "document_class_counts": class_counts,
    }
    (OUT / "KG_P1_R3R_UX2_R1_UNIVERSE_COUNT_RECONCILIATION.json").write_text(json.dumps(recon, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "LOCAL_OUTPUT_DIR.txt").write_text(str(OUT.resolve()), encoding="utf-8")
    print(json.dumps({"status": "LOCAL_PDF_UNIVERSE_FROZEN", "out": str(OUT), **{k: recon[k] for k in ["discovered_pdf_file_count", "unique_pdf_sha256_count", "scientific_paper_file_count", "duplicate_file_copy_count"]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
PDF = PROJECT / "storage/p1_12/discovery/quarantine/sha256/9b/9bd0e84a03a884b54458e1d68ffdd1c9141d66133c8372a94d16cf08e04815ea.pdf"


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def try_pdftotext() -> dict[str, Any]:
    exe = shutil.which("pdftotext")
    if not exe:
        return {"available": False}
    cp = subprocess.run([exe, "-layout", "-f", "1", "-l", "3", str(PDF), "-"], text=True, capture_output=True, timeout=30)
    text = cp.stdout or ""
    return {
        "available": True,
        "returncode": cp.returncode,
        "stdout_chars": len(text),
        "stdout_sha256": sha_text(text),
        "stderr_sha256": sha_text(cp.stderr or ""),
        "sample": text[:800],
    }


def try_pypdf() -> dict[str, Any]:
    if not module_available("pypdf"):
        return {"available": False}
    import pypdf

    reader = pypdf.PdfReader(str(PDF))
    pages = []
    for page in reader.pages[:3]:
        pages.append(page.extract_text() or "")
    text = "\n\n".join(pages)
    return {
        "available": True,
        "page_count": len(reader.pages),
        "text_chars": len(text),
        "text_sha256": sha_text(text),
        "sample": text[:800],
    }


def try_pdfplumber() -> dict[str, Any]:
    if not module_available("pdfplumber"):
        return {"available": False}
    import pdfplumber

    pages = []
    with pdfplumber.open(str(PDF)) as pdf:
        for page in pdf.pages[:3]:
            pages.append(page.extract_text() or "")
        page_count = len(pdf.pages)
    text = "\n\n".join(pages)
    return {
        "available": True,
        "page_count": page_count,
        "text_chars": len(text),
        "text_sha256": sha_text(text),
        "sample": text[:800],
    }


def try_fitz() -> dict[str, Any]:
    if not module_available("fitz"):
        return {"available": False}
    import fitz

    doc = fitz.open(str(PDF))
    pages = []
    for idx in range(min(3, doc.page_count)):
        pages.append(doc.load_page(idx).get_text("text") or "")
    text = "\n\n".join(pages)
    return {
        "available": True,
        "page_count": doc.page_count,
        "text_chars": len(text),
        "text_sha256": sha_text(text),
        "sample": text[:800],
    }


def main() -> None:
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pdf_exists": PDF.exists(),
        "pdf_path_kind": "project_storage_artifact",
        "pdf_size": PDF.stat().st_size if PDF.exists() else None,
        "pdf_sha256": hashlib.sha256(PDF.read_bytes()).hexdigest() if PDF.exists() else None,
        "tools": {
            "pdftotext": try_pdftotext() if PDF.exists() else {"available": False},
            "pypdf": try_pypdf() if PDF.exists() else {"available": False},
            "pdfplumber": try_pdfplumber() if PDF.exists() else {"available": False},
            "fitz": try_fitz() if PDF.exists() else {"available": False},
        },
        "database_writes": 0,
        "trusted_snapshot_promotion_human_decision_writes": 0,
    }
    usable = [
        name for name, detail in report["tools"].items()
        if detail.get("available") and int(detail.get("text_chars") or detail.get("stdout_chars") or 0) > 500
    ]
    report["usable_extractors"] = usable
    report["status"] = "PDF_TEXT_EXTRACTION_AVAILABLE" if usable else "PDF_TEXT_EXTRACTION_UNAVAILABLE"
    write_json("pdf_summary_probe.json", report)
    print(json.dumps({"status": report["status"], "usable_extractors": usable, "pdf_exists": report["pdf_exists"]}, sort_keys=True))


if __name__ == "__main__":
    main()

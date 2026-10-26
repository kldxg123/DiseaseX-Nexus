#!/usr/bin/env python3
import csv
import json
import zipfile
from pathlib import Path

PROJECT = Path("USER_HOME/projects/openclaw")
manifest = PROJECT / "reports/kg_p1_r3r_ux2_r2_70pdf_base_backfill_20260804T082809Z/R2_TARGET_70_FROZEN_MANIFEST.csv"
zip_path = Path("USER_HOME/reports/kg_p1_r3r_ux2_r1_full_paper_reconciliation_backfill_20260804T035410Z/local_input/PDF.zip.complete")
ids = {"r2-target-024-b22c651429a7", "r2-target-051-71634f34b49e"}
rows = []
with manifest.open("r", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        if row.get("target_item_id") in ids:
            rows.append(row)
with zipfile.ZipFile(zip_path) as z:
    names = z.namelist()
    out = []
    for row in rows:
        ref = row.get("source_relative_ref", "")
        candidates = []
        for n in names:
            if n == ref or n == ref.replace("PDF.zip/", "PDF/") or Path(n).name == Path(ref).name:
                candidates.append(n)
        out.append({
            "target_item_id": row.get("target_item_id"),
            "legacy_paper_number": row.get("legacy_paper_number"),
            "source_relative_ref": ref,
            "source_sha256": row.get("source_sha256"),
            "canonical_title": row.get("canonical_title"),
            "candidate_zip_members": candidates[:20],
        })
    nearby = []
    for n in names:
        b = Path(n).name
        low = b.lower()
        if b.startswith(("024_", "051_")) or "openabm" in low or "unequal" in low or "health" in low or "economy" in low:
            nearby.append(n)
    print(json.dumps({"zip_member_count": len(names), "failed_targets": out, "nearby_members": nearby[:80]}, ensure_ascii=False, indent=2))

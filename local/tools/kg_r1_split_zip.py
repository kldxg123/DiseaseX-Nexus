from pathlib import Path
import hashlib

src = Path("reference_materials/infectious_disease_ai_literature_2026_08/PDF.zip")
out = Path("reports/kg_p1_r3r_ux2_r1_full_paper_reconciliation_backfill_20260804T035410Z/local_input_chunks")
out.mkdir(parents=True, exist_ok=True)
for p in out.glob("PDF.zip.part*"):
    p.unlink()
h = hashlib.sha256()
size = 20 * 1024 * 1024
idx = 0
with src.open("rb") as f:
    while True:
        data = f.read(size)
        if not data:
            break
        h.update(data)
        (out / f"PDF.zip.part{idx:03d}").write_bytes(data)
        idx += 1
(out / "PDF.zip.sha256").write_text(h.hexdigest() + "\n", encoding="utf-8")
print({"parts": idx, "sha256": h.hexdigest(), "bytes": src.stat().st_size})

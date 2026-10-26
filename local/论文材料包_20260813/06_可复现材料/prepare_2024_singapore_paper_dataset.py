from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path.cwd()
SOURCE = ROOT / "tmp" / "sg_weekly_infectious_all.json"
OUT = ROOT / "remote_edit" / "post2024_exact_inputs"
DATASET_ID = "d_ca168b2cb763640d72c4600a68f9909e"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


payload = json.loads(SOURCE.read_text(encoding="utf-8"))
if not payload.get("success"):
    raise RuntimeError("official data.gov.sg response was not successful")

rows = []
for row in payload["result"]["records"]:
    epi_week = row["epi_week"]
    if row["disease"] != "Dengue Fever":
        continue
    if not ("2012-W01" <= epi_week <= "2019-W48"):
        continue
    year, week = epi_week.split("-W")
    rows.append(
        {
            "year": int(year),
            "week": int(week),
            "cases": int(row["no._of_cases"]),
            "vault_id": int(row["_id"]),
        }
    )

rows.sort(key=lambda value: (value["year"], value["week"]))
if len(rows) != 413:
    raise RuntimeError(f"unexpected official row count: {len(rows)}")

# The official catalogue contains 413 rows while the paper states 412 points.
# Preserve the extra 2014-W53 source row in the manifest and exclude it only for
# the paper-aligned reconstruction. The paper does not document this preprocessing
# decision, so the exclusion is an explicit reproducibility assumption, not a
# claim that the epidemiological-week record is invalid.
excluded = [row for row in rows if row["year"] == 2014 and row["week"] == 53]
canonical = [row for row in rows if row not in excluded]
if len(canonical) != 412:
    raise RuntimeError(f"unexpected canonical row count: {len(canonical)}")

OUT.mkdir(parents=True, exist_ok=True)
csv_path = OUT / "singapore_dengue_official_paper_aligned_2012_2019.csv"
with csv_path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["year", "week", "cases", "vault_id"])
    writer.writeheader()
    writer.writerows(canonical)

manifest = {
    "source": f"Singapore Ministry of Health via data.gov.sg dataset {DATASET_ID}",
    "source_page": f"https://data.gov.sg/datasets/{DATASET_ID}/view",
    "source_api": f"https://data.gov.sg/api/action/datastore_search?resource_id={DATASET_ID}&limit=25000",
    "source_response_bytes": SOURCE.stat().st_size,
    "source_response_sha256": sha256(SOURCE),
    "source_total_records": payload["result"]["total"],
    "dengue_rows_in_requested_range_raw": len(rows),
    "canonical_rows": len(canonical),
    "canonical_csv_sha256": sha256(csv_path),
    "range": {"first": "2012-W01", "last": "2019-W48"},
    "excluded_source_rows": excluded,
    "exclusion_reason": "explicit reconstruction assumption used to reconcile the official 413-row interval with the paper's stated 412-point series; the paper does not document which row was omitted",
    "exclusion_sensitivity_note": "2014-W53 remains preserved in the raw official response and must not be treated as intrinsically invalid",
    "paper_split_note": "paper reports 412 source points but 311+50+50=411 labels; the first canonical row is retained as lag warm-up and the remaining 411 labels follow the published split sizes",
}
(OUT / "official_paper_dataset_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(manifest, ensure_ascii=False))

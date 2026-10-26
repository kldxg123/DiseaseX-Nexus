from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(r"F:\作业\aibola")
SOURCE_FILES = [ROOT / "tmp" / "sg_dengue_rows_0.json", ROOT / "tmp" / "sg_dengue_rows_500.json"]
OUT = ROOT / "remote_edit" / "post2024_inputs"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


rows: list[dict[str, object]] = []
for path in SOURCE_FILES:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"official API error in {path.name}")
    for row in payload["data"]["rows"]:
        if row["type_dengue"] != "Dengue":
            continue
        raw_value = str(row.get("number", "")).strip()
        rows.append(
            {
                "year": int(row["year"]),
                "week": int(row["eweek"]),
                "cases": None if raw_value in {"", "na", "NA", "-"} else int(raw_value),
                "vault_id": row["vault_id"],
            }
        )
rows.sort(key=lambda row: (int(row["year"]), int(row["week"])))
OUT.mkdir(parents=True, exist_ok=True)
csv_path = OUT / "singapore_dengue_official_2014_2018.csv"
with csv_path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["year", "week", "cases", "vault_id"])
    writer.writeheader()
    writer.writerows(rows)
missing = [row for row in rows if row["cases"] is None]
manifest = {
    "source": "Singapore government data.gov.sg official dataset d_ac1eecf0886ff0bceefbc51556247015",
    "source_url": "https://data.gov.sg/datasets/d_ac1eecf0886ff0bceefbc51556247015/view",
    "api_url_pattern": "https://api-production.data.gov.sg/v2/public/api/datasets/d_ac1eecf0886ff0bceefbc51556247015/list-rows",
    "source_response_sha256": {path.name: sha256(path) for path in SOURCE_FILES},
    "csv_sha256": sha256(csv_path),
    "rows": len(rows),
    "missing_rows": len(missing),
    "missing_keys": [{"year": row["year"], "week": row["week"]} for row in missing],
    "range": {"first": [rows[0]["year"], rows[0]["week"]], "last": [rows[-1]["year"], rows[-1]["week"]]},
    "relationship_to_2024_paper": "official overlapping 2014-2018 subset; not the complete 2012-2019 412-point paper dataset",
}
(OUT / "official_overlap_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False))

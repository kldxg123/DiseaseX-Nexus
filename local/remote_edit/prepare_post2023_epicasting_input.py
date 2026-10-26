from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(r"F:\作业\aibola\tmp\Epicasting-3740776f65c6f4157e67e6a78f71daeade45246b")
OUT = Path(r"F:\作业\aibola\remote_edit\post2023_inputs")
SOURCE = ROOT / "Datasets" / "Dengue_datasets" / "Singapore_Dengue.xlsx"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


OUT.mkdir(parents=True, exist_ok=True)
frame = pd.read_excel(SOURCE, sheet_name="in")
frame.columns = [str(column).strip().lower() for column in frame.columns]
frame = frame[["year", "week", "cases"]].copy()
frame["year"] = pd.to_numeric(frame["year"], errors="raise").astype(int)
frame["week"] = pd.to_numeric(frame["week"], errors="raise").astype(int)
frame["cases"] = pd.to_numeric(frame["cases"], errors="raise").astype(float)
if frame.isna().any().any() or len(frame) != 838:
    raise RuntimeError("unexpected Singapore dengue workbook shape or missing value")
csv_path = OUT / "singapore_dengue_epicasting.csv"
frame.to_csv(csv_path, index=False, lineterminator="\n")
meta = {
    "source_workbook": str(SOURCE.relative_to(ROOT)),
    "source_workbook_sha256": sha256(SOURCE),
    "derived_csv": csv_path.name,
    "derived_csv_sha256": sha256(csv_path),
    "rows": len(frame),
    "first_key": {"year": int(frame.iloc[0].year), "week": int(frame.iloc[0].week)},
    "last_key": {"year": int(frame.iloc[-1].year), "week": int(frame.iloc[-1].week)},
    "missing_values": int(frame.isna().sum().sum()),
    "test_horizon_weeks": 13,
    "validation_weeks": 26,
    "train_weeks": len(frame) - 39,
}
(OUT / "input_manifest.json").write_text(
    json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(meta, ensure_ascii=False))

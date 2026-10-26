from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(r"F:\作业\aibola\tmp\Epicasting-3740776f65c6f4157e67e6a78f71daeade45246b")


def serialise(value: object) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


items: list[dict[str, object]] = []
for path in sorted(ROOT.rglob("*.xlsx")):
    book = pd.ExcelFile(path)
    sheets = []
    for sheet_name in book.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet_name, header=None)
        sheets.append(
            {
                "sheet": sheet_name,
                "shape": list(frame.shape),
                "head": [
                    [serialise(value) for value in row]
                    for row in frame.head(8).itertuples(index=False, name=None)
                ],
                "tail": [
                    [serialise(value) for value in row]
                    for row in frame.tail(5).itertuples(index=False, name=None)
                ],
            }
        )
    items.append({"path": str(path.relative_to(ROOT)), "sheets": sheets})

print(json.dumps(items, ensure_ascii=False, indent=2))

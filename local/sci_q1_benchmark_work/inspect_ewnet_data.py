from __future__ import annotations

from pathlib import Path

import pandas as pd


root = Path.home() / "projects/openclaw/experiments/sci_q1_ewnet_optimization_20260822/Epicasting"
path = root / "Datasets/Dengue_datasets/Singapore_Dengue.xlsx"
book = pd.ExcelFile(path)
print("sheets=", book.sheet_names)
for sheet in book.sheet_names:
    frame = pd.read_excel(path, sheet_name=sheet)
    print("sheet=", sheet, "shape=", frame.shape)
    print("columns=", [str(item) for item in frame.columns])
    print(frame.head(8).to_string(index=False))
    print(frame.tail(8).to_string(index=False))

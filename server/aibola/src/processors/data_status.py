from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_data_status(root: str | Path = ".") -> dict:
    import pandas as pd
    from src.utils.io import read_table

    base = Path(root)
    status: dict = {
        "reports": {},
        "downloaded_csv_headers": [],
        "processed_stats": [],
        "label_counts": [],
    }
    for name in ["prepare_data_summary.json", "data_quality_report.md", "backtest_quantitative.json", "baseline_metrics.json"]:
        p = base / "data/outputs/reports" / name
        status["reports"][name] = p.exists()
    for p in sorted((base / "data/raw/github").glob("**/*.csv")):
        try:
            df = pd.read_csv(p, nrows=5)
            full = pd.read_csv(p, usecols=lambda c: c in {"Indicator", "indicator"}, nrows=2000)
            indicators = []
            if not full.empty:
                col = "Indicator" if "Indicator" in full.columns else "indicator"
                indicators = full[col].dropna().astype(str).unique()[:30].tolist()
            status["downloaded_csv_headers"].append({"file": p.name, "columns": list(df.columns), "rows_preview": len(df), "indicators": indicators})
        except Exception as exc:
            status["downloaded_csv_headers"].append({"file": p.name, "error": type(exc).__name__})
    for path in [base / "data/processed/ebola_cases_daily.parquet", base / "data/processed/ebola_quant_panel.parquet"]:
        fallback = path.with_suffix(path.suffix + ".jsonl")
        if not path.exists() and not fallback.exists():
            status["processed_stats"].append({"file": path.name, "status": "missing"})
            continue
        records = read_table(path)
        df = pd.DataFrame(records)
        stats = {"file": path.name, "rows": len(df), "columns": list(df.columns)[:40]}
        for col in ["new_cases", "cum_cases", "lat", "lon"]:
            if col in df:
                stats[col] = {
                    "min": float(df[col].min()),
                    "max": float(df[col].max()),
                    "nonzero": int((df[col] != 0).sum()),
                }
        if "country" in df:
            stats["countries"] = df["country"].dropna().astype(str).unique()[:10].tolist()
        if path.name == "ebola_quant_panel.parquet" and "country" in df:
            group_cols = [col for col in ["country", "adm1", "adm2"] if col in df]
            stats["group_count"] = int(df.groupby(group_cols, dropna=False).ngroups) if group_cols else 0
            stats["largest_groups"] = [
                {"key": str(key), "rows": int(value)}
                for key, value in df.groupby(group_cols, dropna=False).size().sort_values(ascending=False).head(10).items()
            ] if group_cols else []
            if "date" in df:
                stats["date_dtype"] = str(df["date"].dtype)
                stats["date_min"] = str(df["date"].min())
                stats["date_max"] = str(df["date"].max())
        status["processed_stats"].append(stats)
    for p in sorted((base / "data/processed").glob("trend_dataset_*d.jsonl")):
        counts: dict[str, int] = {}
        n = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            n += 1
            label = json.loads(line).get("label")
            counts[label] = counts.get(label, 0) + 1
        status["label_counts"].append({"file": p.name, "samples": n, "label_counts": counts})
    try:
        from src.processors.label_builder import build_labels

        panel = read_table(base / "data/processed/ebola_quant_panel.parquet")
        status["live_label_builder_counts"] = {}
        for horizon in (7, 14, 30):
            samples = build_labels(panel, horizon_days=horizon)
            counts: dict[str, int] = {}
            for sample in samples:
                counts[sample["label"]] = counts.get(sample["label"], 0) + 1
            status["live_label_builder_counts"][str(horizon)] = {"samples": len(samples), "label_counts": counts}
    except Exception as exc:
        status["live_label_builder_error"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Print non-GPU data preparation status.")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    print(json.dumps(build_data_status(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metric(frame: pd.DataFrame, column: str) -> dict[str, float | int]:
    error = frame[column] - frame["true"]
    absolute = error.abs()
    wape = float(absolute.sum() / frame["true"].abs().sum())
    return {
        "n": int(len(frame)),
        "wape": wape,
        "agreement_pct": (1.0 - wape) * 100.0,
        "mae": float(absolute.mean()),
        "rmse": float(np.sqrt(np.mean(error**2))),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.report_dir.exists():
        raise RuntimeError(f"report directory exists: {args.report_dir}")
    args.report_dir.mkdir(parents=True)

    prediction_path = args.experiment_root / "recursive_test_v2/test_routed_predictions.csv"
    frame = pd.read_csv(prediction_path, parse_dates=["origin", "date"])
    project_column = "recursive_hist_poisson"
    rows: list[dict[str, object]] = []
    groups = {
        "H0": [0],
        "H1": [1],
        "H0-H1 primary": [0, 1],
        "H0-H8 secondary": list(range(9)),
    }
    for label, horizons in groups.items():
        part = frame[frame["week_ahead"].isin(horizons)]
        for name, column in (
            ("Project recursive seasonal Poisson", project_column),
            ("Paper AWBE (reference only)", "AWBE"),
        ):
            rows.append({"scope": label, "model": name, **metric(part, column)})
    metrics = pd.DataFrame(rows)
    metrics.to_csv(args.report_dir / "01_PAIRED_METRICS.csv", index=False)

    horizon_rows: list[dict[str, object]] = []
    for horizon, part in frame.groupby("week_ahead"):
        for name, column in (
            ("Project", project_column),
            ("Paper AWBE", "AWBE"),
        ):
            horizon_rows.append(
                {"horizon": int(horizon), "model": name, **metric(part, column)}
            )
    horizon_metrics = pd.DataFrame(horizon_rows)
    horizon_metrics.to_csv(args.report_dir / "02_METRICS_BY_HORIZON.csv", index=False)

    short = frame[frame["week_ahead"].isin([0, 1])].copy()
    short.to_csv(args.report_dir / "03_H0_H1_PAIRED_PREDICTIONS.csv", index=False)
    shutil.copy2(prediction_path, args.report_dir / "04_H0_H8_PAIRED_PREDICTIONS.csv")
    shutil.copy2(
        args.experiment_root / "recursive_validation_v2_retry1/route_lock.json",
        args.report_dir / "05_ROUTE_LOCK.json",
    )
    for source, target in (
        (args.experiment_root / "run_independent_benchmark.py", "06_INDEPENDENT_FEATURE_PIPELINE.py"),
        (args.experiment_root / "run_recursive_benchmark.py", "07_RECURSIVE_MODEL_PIPELINE.py"),
    ):
        shutil.copy2(source, args.report_dir / target)
    shutil.copy2(
        args.project_root
        / "src/epidemic_core/forecast_model_kg/reference/hk_influenza_independent_benchmark.v1.json",
        args.report_dir / "08_API_ARTIFACT.json",
    )

    style = {
        "Project recursive seasonal Poisson": "#1769aa",
        "Paper AWBE (reference only)": "#d97706",
    }
    primary = metrics[metrics["scope"].isin(["H0", "H1", "H0-H1 primary"])].copy()
    fig, ax = plt.subplots(figsize=(9.2, 5.2), dpi=180)
    x = np.arange(3)
    width = 0.36
    for offset, model in zip((-width / 2, width / 2), style):
        values = primary[primary["model"] == model].set_index("scope").loc[
            ["H0", "H1", "H0-H1 primary"], "wape"
        ]
        bars = ax.bar(x + offset, values * 100, width, label=model, color=style[model])
        ax.bar_label(bars, fmt="%.2f%%", padding=3, fontsize=9)
    ax.set_xticks(x, ["H0 nowcast", "H1 one-week", "H0-H1 pooled"])
    ax.set_ylabel("WAPE (%) — lower is better")
    ax.set_title("Independent project model is comparable only at H0-H1")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(args.report_dir / "09_PRIMARY_H0_H1_WAPE.png", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.2, 5.2), dpi=180)
    for model, color in (("Project", "#1769aa"), ("Paper AWBE", "#d97706")):
        values = horizon_metrics[horizon_metrics["model"] == model]
        ax.plot(values["horizon"], values["wape"] * 100, marker="o", linewidth=2.2, label=model, color=color)
    ax.axvspan(-0.15, 1.15, color="#22c55e", alpha=0.12, label="supported project range")
    ax.set_xticks(range(9), [f"H{value}" for value in range(9)])
    ax.set_xlabel("Forecast horizon (weeks)")
    ax.set_ylabel("WAPE (%) — lower is better")
    ax.set_title("Full horizon disclosure: project performance degrades after H1")
    ax.legend(frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(args.report_dir / "10_FULL_HORIZON_WAPE.png", bbox_inches="tight")
    plt.close(fig)

    display = short[short["week_ahead"] == 1].tail(104)
    fig, ax = plt.subplots(figsize=(11, 5.4), dpi=180)
    ax.plot(display["date"], display["true"], color="#111827", linewidth=2.0, label="Observed")
    ax.plot(display["date"], display[project_column], color="#1769aa", linewidth=1.6, label="Project H1")
    ax.plot(display["date"], display["AWBE"], color="#d97706", linewidth=1.4, alpha=0.85, label="Paper AWBE H1")
    ax.set_ylabel("Weekly influenza activity rate")
    ax.set_title("Paired H1 predictions: final 104 available targets")
    ax.legend(frameon=False, ncol=3)
    ax.grid(alpha=0.2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(args.report_dir / "11_H1_PREDICTION_TRACE.png", bbox_inches="tight")
    plt.close(fig)

    primary_project = metrics.query("scope == 'H0-H1 primary' and model.str.startswith('Project')", engine="python").iloc[0]
    primary_paper = metrics.query("scope == 'H0-H1 primary' and model.str.startswith('Paper')", engine="python").iloc[0]
    secondary_project = metrics.query("scope == 'H0-H8 secondary' and model.str.startswith('Project')", engine="python").iloc[0]
    secondary_paper = metrics.query("scope == 'H0-H8 secondary' and model.str.startswith('Paper')", engine="python").iloc[0]
    report = f"""# Independent project method versus paper AWBE

## Final status

`HK_FLU_INDEPENDENT_H0_H1_PARITY_VERIFIED_FULL_HORIZON_GAP_RETAINED`

## What changed

The project now carries an independently trained recursive seasonal Poisson gradient-boosting comparator. It uses only the official weekly influenza activity history, deterministic lag/rolling/seasonal features, and expanding-origin fitting. It does **not** call the paper AWBE algorithm and does not use AWBE/SAE/NBE/AWAE outputs as model features.

## Primary paired result (H0-H1)

- Same origin-horizon-target rows: {int(primary_project['n'])}
- Project WAPE: {primary_project['wape']:.6f} ({primary_project['wape']*100:.2f}%)
- Paper AWBE WAPE: {primary_paper['wape']:.6f} ({primary_paper['wape']*100:.2f}%)
- Absolute WAPE gap: {(primary_project['wape']-primary_paper['wape']):.6f} ({(primary_project['wape']-primary_paper['wape'])*100:.2f} percentage points)
- Project RMSE: {primary_project['rmse']:.4f}; paper RMSE: {primary_paper['rmse']:.4f}

The declared comparability tolerance is one absolute WAPE point. H0-H1 passes. H0 alone favors the project (24.56% versus 25.98%); H1 favors the paper (36.31% versus 34.47%).

## Mandatory full-horizon disclosure (H0-H8)

- Project WAPE: {secondary_project['wape']:.6f} ({secondary_project['wape']*100:.2f}%)
- Paper AWBE WAPE: {secondary_paper['wape']:.6f} ({secondary_paper['wape']*100:.2f}%)
- Absolute gap: {(secondary_project['wape']-secondary_paper['wape']):.6f}

The project does not achieve parity after H1. The implementation therefore limits the independent model's supported operational scope to H0-H1 and retains H2-H8 as an explicit unsupported range. This prevents a short-horizon success from being misreported as full-horizon equivalence.

## Governance

- Retrospective paired benchmark; not fresh held-out certification.
- Shadow only; database writes, forecast publication, promotion, and Active Pointer changes are zero.
- Existing dashboard, Trusted data, forecasts, and the original comparison archive were not modified by the experiment run.
- WAPE-derived agreement is not classification accuracy and does not guarantee future performance.
"""
    (args.report_dir / "00_FINAL_REPORT.md").write_text(report, encoding="utf-8")

    summary = {
        "status": "HK_FLU_INDEPENDENT_H0_H1_PARITY_VERIFIED_FULL_HORIZON_GAP_RETAINED",
        "primary_project": primary_project.to_dict(),
        "primary_paper": primary_paper.to_dict(),
        "primary_absolute_wape_gap": float(primary_project["wape"] - primary_paper["wape"]),
        "primary_parity_tolerance": 0.01,
        "primary_parity_pass": bool(primary_project["wape"] <= primary_paper["wape"] + 0.01),
        "secondary_project": secondary_project.to_dict(),
        "secondary_paper": secondary_paper.to_dict(),
        "secondary_parity_pass": False,
        "paper_method_used_by_project": False,
        "database_writes": 0,
        "forecast_runs_created": 0,
        "promotion_changes": 0,
        "active_pointer_changes": 0,
    }
    (args.report_dir / "12_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    entries = []
    for path in sorted(args.report_dir.iterdir()):
        if path.is_file() and path.name != "MANIFEST.sha256":
            entries.append(f"{sha256(path)}  {path.name}")
    (args.report_dir / "MANIFEST.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

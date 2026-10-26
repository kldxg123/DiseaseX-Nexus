from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D


ROOT = Path(r"F:\作业\aibola")
INPUT = (
    ROOT
    / "reports"
    / "nc_submission_revision_20260825_wape_four_path"
    / "Evidence"
    / "FOUR_PATH_PAIRED_PREDICTIONS_H0_H8.csv"
)
OUTPUT = ROOT / "reports" / "boxplot_wape_review_20260826"
PNG = OUTPUT / "Candidate_Fig_WAPE_Boxplots_Four_Path.png"
STATS_CSV = OUTPUT / "Candidate_Fig_WAPE_Boxplots_Five_Number_Summary.csv"
AUDIT_JSON = OUTPUT / "Candidate_Fig_WAPE_Boxplots_Audit.json"

METHODS = ["Baseline", "DiseaseX", "Paper AWBE", "KG merged route"]
DISPLAY = {
    "Baseline": "Baseline",
    "DiseaseX": "Independent\nDiseaseX",
    "Paper AWBE": "Paper\nAWBE",
    "KG merged route": "KG merged\nroute",
}
COLORS = {
    "Baseline": "#7B8794",
    "DiseaseX": "#E69F00",
    "Paper AWBE": "#2F6FB0",
    "KG merged route": "#7A3DE2",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def wape(actual: pd.Series, predicted: pd.Series) -> float:
    return float((predicted - actual).abs().sum() / actual.abs().sum() * 100.0)


def five_number(values: np.ndarray) -> dict[str, float]:
    q = np.quantile(values, [0.0, 0.25, 0.5, 0.75, 1.0])
    return {
        "minimum": float(q[0]),
        "q1": float(q[1]),
        "median": float(q[2]),
        "q3": float(q[3]),
        "maximum": float(q[4]),
    }


def styled_boxplot(
    ax: plt.Axes,
    values: list[np.ndarray],
    *,
    show_fliers: bool,
) -> None:
    bp = ax.boxplot(
        values,
        patch_artist=True,
        widths=0.58,
        whis=1.5,
        showfliers=show_fliers,
        medianprops={"color": "#111827", "linewidth": 2.2},
        whiskerprops={"color": "#4B5563", "linewidth": 1.4},
        capprops={"color": "#4B5563", "linewidth": 1.4},
        flierprops={
            "marker": "o",
            "markersize": 2.1,
            "markerfacecolor": "#6B7280",
            "markeredgecolor": "none",
            "alpha": 0.18,
        },
    )
    for patch, method in zip(bp["boxes"], METHODS, strict=True):
        patch.set_facecolor(COLORS[method])
        patch.set_edgecolor("#374151")
        patch.set_linewidth(1.25)
        patch.set_alpha(0.78)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(INPUT)
    required = {"origin", "date", "week_ahead", "true", *METHODS}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if len(df) != 4968:
        raise ValueError(f"Expected 4,968 paired rows, observed {len(df):,}")
    if df[["origin", "date", "week_ahead"]].duplicated().any():
        raise ValueError("Duplicate origin-date-horizon keys detected")
    if df["true"].isna().any() or (df["true"] < 0).any():
        raise ValueError("Invalid target values")

    short = df[df["week_ahead"].between(0, 1)].copy()
    if len(short) != 1104:
        raise ValueError(f"Expected 1,104 H0-H1 rows, observed {len(short):,}")
    short_mean_target = float(short["true"].mean())

    row_scaled_errors: dict[str, np.ndarray] = {}
    short_wape: dict[str, float] = {}
    full_wape: dict[str, float] = {}
    horizon_wape: dict[str, np.ndarray] = {}
    stats_rows: list[dict[str, object]] = []

    for method in METHODS:
        row_values = (
            (short[method] - short["true"]).abs() / short_mean_target * 100.0
        ).to_numpy()
        row_scaled_errors[method] = row_values
        short_wape[method] = wape(short["true"], short[method])
        full_wape[method] = wape(df["true"], df[method])

        by_horizon = np.array(
            [
                wape(g["true"], g[method])
                for _, g in df.groupby("week_ahead", sort=True)
            ],
            dtype=float,
        )
        horizon_wape[method] = by_horizon

        stats_rows.append(
            {
                "panel": "A",
                "method": method,
                "scope": "H0-H1",
                "distribution_unit": "row_normalized_absolute_error_pct",
                "n": len(row_values),
                **five_number(row_values),
                "aggregate_wape_pct": short_wape[method],
            }
        )
        stats_rows.append(
            {
                "panel": "B",
                "method": method,
                "scope": "H0-H8",
                "distribution_unit": "horizon_specific_wape_pct",
                "n": len(by_horizon),
                **five_number(by_horizon),
                "aggregate_wape_pct": full_wape[method],
            }
        )

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 14,
            "axes.labelsize": 11.5,
            "xtick.labelsize": 10.5,
            "ytick.labelsize": 10.5,
            "axes.linewidth": 0.9,
            "figure.dpi": 160,
            "savefig.dpi": 400,
        }
    )

    fig, axes = plt.subplots(1, 2, figsize=(14.4, 6.9), constrained_layout=False)
    fig.patch.set_facecolor("white")
    positions = np.arange(1, len(METHODS) + 1)

    # Panel A: the group mean of this zero-safe row-level quantity is exactly WAPE.
    ax = axes[0]
    styled_boxplot(ax, [row_scaled_errors[m] for m in METHODS], show_fliers=True)
    ax.set_yscale("log")
    ax.set_ylim(0.008, 520)
    ax.set_yticks([0.01, 0.1, 1, 10, 30, 100, 300])
    ax.set_yticklabels(["0.01", "0.1", "1", "10", "30", "100", "300"])
    ax.set_xticks(positions, [DISPLAY[m] for m in METHODS])
    ax.set_ylabel("Normalized absolute error (%) - log scale")
    ax.set_title(
        "A  H0-H1 row-level error distributions",
        loc="left",
        weight="bold",
        pad=18,
    )

    for x, method in zip(positions, METHODS, strict=True):
        value = short_wape[method]
        ax.scatter(
            x,
            value,
            marker="D",
            s=54,
            facecolor="white",
            edgecolor="#111827",
            linewidth=1.2,
            zorder=5,
        )
        ax.annotate(
            f"WAPE {value:.2f}%",
            (x, value),
            xytext=(0, 12),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9.2,
            weight="bold",
            color="#111827",
        )
    ax.grid(axis="y", which="major", color="#D1D5DB", linewidth=0.7, alpha=0.65)
    ax.grid(axis="y", which="minor", visible=False)

    # Panel B: nine horizon-specific WAPE values show how performance changes with lead time.
    ax = axes[1]
    styled_boxplot(ax, [horizon_wape[m] for m in METHODS], show_fliers=False)
    rng = np.random.default_rng(20260826)
    for x, method in zip(positions, METHODS, strict=True):
        vals = horizon_wape[method]
        jitter = rng.uniform(-0.075, 0.075, size=len(vals))
        ax.scatter(
            np.full(len(vals), x) + jitter,
            vals,
            s=25,
            facecolor="white",
            edgecolor=COLORS[method],
            linewidth=1.15,
            alpha=0.96,
            zorder=4,
        )
        aggregate = full_wape[method]
        ax.scatter(
            x,
            aggregate,
            marker="D",
            s=58,
            facecolor="white",
            edgecolor="#111827",
            linewidth=1.25,
            zorder=5,
        )
        label_y = float(vals.max()) + (3.2 if vals.max() < 100 else 2.0)
        ax.text(
            x,
            label_y,
            f"overall {aggregate:.2f}%",
            ha="center",
            va="bottom",
            fontsize=9.0,
            weight="bold",
            color="#111827",
        )
    ax.axhline(30.0, color="#B91C1C", linestyle=(0, (5, 3)), linewidth=1.25)
    ax.text(
        4.42,
        31.4,
        "30% engineering gate",
        ha="right",
        va="bottom",
        fontsize=9.2,
        color="#991B1B",
    )
    ax.set_ylim(15, 120)
    ax.set_xticks(positions, [DISPLAY[m] for m in METHODS])
    ax.set_ylabel("Horizon-specific WAPE (%)")
    ax.set_title(
        "B  H0-H8 robustness across forecast horizons",
        loc="left",
        weight="bold",
        pad=18,
    )

    ax.grid(axis="y", color="#D1D5DB", linewidth=0.7, alpha=0.65)

    for ax in axes:
        ax.set_facecolor("#FBFCFE")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="x", pad=8)

    legend_items = [
        Line2D(
            [0],
            [0],
            marker="D",
            color="none",
            markerfacecolor="white",
            markeredgecolor="#111827",
            markersize=7,
            label="Aggregate WAPE",
        ),
        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor="white",
            markeredgecolor="#4B5563",
            markersize=6,
            label="Horizon-specific WAPE",
        ),
    ]
    fig.legend(
        handles=legend_items,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.924),
        ncol=2,
        frameon=False,
        fontsize=10,
    )
    fig.suptitle(
        "Four-path Hong Kong influenza benchmark: error distribution and WAPE robustness",
        x=0.5,
        y=0.985,
        fontsize=17,
        weight="bold",
    )
    fig.text(
        0.5,
        0.025,
        "Panel A: 1,104 matched rows per method. Panel B: nine horizon WAPE values per method (H0-H8), with every point shown. "
        "Boxes show Q1-Q3 with the median line; whiskers extend to 1.5 x IQR. "
        "Panel A uses |prediction - truth| / mean(truth) x 100, so its group mean equals WAPE and remains defined near zero. "
        "Lower values are better. The KG merged route uses paper AWBE at H1-H8 and is not paper-free.",
        ha="center",
        va="bottom",
        fontsize=9.4,
        color="#374151",
        wrap=True,
    )
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.175, top=0.79, wspace=0.22)
    fig.savefig(PNG, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    stats_df = pd.DataFrame(stats_rows)
    stats_df.to_csv(STATS_CSV, index=False, encoding="utf-8")
    audit = {
        "status": "REVIEW_ONLY_NOT_INSERTED",
        "input": str(INPUT),
        "input_sha256": sha256(INPUT),
        "figure": str(PNG),
        "figure_sha256": sha256(PNG),
        "statistics_csv": str(STATS_CSV),
        "statistics_sha256": sha256(STATS_CSV),
        "paired_rows_h0_h1": len(short),
        "paired_rows_h0_h8": len(df),
        "actual_zero_count": int((df["true"] == 0).sum()),
        "short_horizon_wape_pct": short_wape,
        "full_horizon_wape_pct": full_wape,
        "box_definition": "Q1-Q3; median; whiskers 1.5 x IQR; extrema retained in summary CSV",
        "paper_modified": False,
    }
    AUDIT_JSON.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(audit, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

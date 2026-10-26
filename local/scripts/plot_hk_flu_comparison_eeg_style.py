from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "work" / "natcomm_wape_optimization_20260825" / "outputs"
OUTPUT = ROOT / "reports" / "hk_flu_comparison_eeg_style_20260827"
SEED = 20260827

COMPONENT_MODELS = [
    "ARIMA",
    "GARCH",
    "RF",
    "XGB",
    "InTimePlus",
    "TSTPlus",
    "LSTM",
    "GRU",
]
ENSEMBLE_MODELS = ["SAE", "NBE", "AWAE", "AWBE", "KG_ADAPTIVE_ENSEMBLE"]
ALL_MODELS = COMPONENT_MODELS + ["Baseline"] + ENSEMBLE_MODELS + ["KG_HORIZON_ROUTER"]
FOCAL_MODELS = ["Baseline", "KG_ADAPTIVE_ENSEMBLE", "AWBE", "KG_HORIZON_ROUTER"]

LABELS = {
    "ARIMA": "ARIMA",
    "GARCH": "GARCH",
    "RF": "RF",
    "XGB": "XGB",
    "InTimePlus": "InTimePlus",
    "TSTPlus": "TSTPlus",
    "LSTM": "LSTM",
    "GRU": "GRU",
    "Baseline": "Constant baseline",
    "SAE": "Paper SAE",
    "NBE": "Paper NBE",
    "AWAE": "Paper AWAE",
    "AWBE": "Paper AWBE",
    "KG_ADAPTIVE_ENSEMBLE": "DiseaseX independent",
    "KG_HORIZON_ROUTER": "KG horizon router*",
}

COLORS = {
    "teal": "#2A9D8F",
    "orange": "#E76F51",
    "blue": "#6B8EAD",
    "yellow": "#D8C772",
    "red": "#D1495B",
    "purple": "#7B6EA8",
    "green": "#5BAE8B",
    "gray": "#8B95A1",
    "dark": "#263238",
    "light_grid": "#D8DEE5",
}

MODEL_COLORS = {
    "Baseline": COLORS["gray"],
    "KG_ADAPTIVE_ENSEMBLE": COLORS["orange"],
    "AWBE": COLORS["blue"],
    "KG_HORIZON_ROUTER": COLORS["purple"],
    "AWAE": COLORS["teal"],
}

FAMILY_ORDER = ["Statistical", "Machine learning", "Deep learning", "Ensemble", "Orchestration"]
FAMILY_COLORS = {
    "Statistical": COLORS["red"],
    "Machine learning": COLORS["blue"],
    "Deep learning": COLORS["teal"],
    "Ensemble": COLORS["orange"],
    "Orchestration": COLORS["purple"],
}

HORIZON_BANDS = {
    "Nowcast (0)": [0],
    "Short (1-2)": [1, 2],
    "Medium (3-5)": [3, 4, 5],
    "Long (6-8)": [6, 7, 8],
}


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "figure.dpi": 140,
            "savefig.dpi": 300,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#77818C",
            "axes.linewidth": 0.8,
            "grid.color": COLORS["light_grid"],
            "grid.linewidth": 0.6,
            "grid.alpha": 0.75,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def family(model: str) -> str:
    if model == "KG_HORIZON_ROUTER":
        return "Orchestration"
    if model in {"ARIMA", "GARCH", "Baseline"}:
        return "Statistical"
    if model in {"RF", "XGB"}:
        return "Machine learning"
    if model in {"InTimePlus", "TSTPlus", "LSTM", "GRU"}:
        return "Deep learning"
    return "Ensemble"


def horizon_band(horizon: int) -> str:
    for name, values in HORIZON_BANDS.items():
        if horizon in values:
            return name
    raise ValueError(horizon)


def wape(actual: np.ndarray | pd.Series, predicted: np.ndarray | pd.Series) -> float:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    denominator = np.abs(y).sum()
    if denominator <= 0:
        raise ValueError("WAPE denominator is zero")
    return float(np.abs(y - p).sum() / denominator)


def metric_panel(frame: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    records: list[dict[str, float | int | str]] = []
    for model in models:
        records.append(
            {
                "model": model,
                "week_ahead": -1,
                "n": len(frame),
                "wape": wape(frame["true"], frame[model]),
                "mae": float(np.abs(frame[model] - frame["true"]).mean()),
                "rmse": float(np.sqrt(np.mean((frame[model] - frame["true"]) ** 2))),
            }
        )
        for horizon, part in frame.groupby("week_ahead", sort=True):
            records.append(
                {
                    "model": model,
                    "week_ahead": int(horizon),
                    "n": len(part),
                    "wape": wape(part["true"], part[model]),
                    "mae": float(np.abs(part[model] - part["true"]).mean()),
                    "rmse": float(np.sqrt(np.mean((part[model] - part["true"]) ** 2))),
                }
            )
    result = pd.DataFrame(records)
    result["family"] = result["model"].map(family)
    return result


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    main = pd.read_csv(INPUT / "test_paired_predictions.csv", parse_dates=["origin", "date"])
    router = pd.read_csv(INPUT / "router_paired_predictions.csv", parse_dates=["origin", "date"])
    keys = ["origin", "date", "week_ahead"]
    main = main.merge(
        router[keys + ["KG_HORIZON_ROUTER"]],
        on=keys,
        how="inner",
        validate="one_to_one",
    )
    external = pd.read_csv(
        INPUT / "postcovid_paired_predictions.csv", parse_dates=["origin", "date"]
    )
    required = {"origin", "date", "week_ahead", "true", *ALL_MODELS}
    for name, frame in [("main", main), ("external", external)]:
        missing = required - set(frame.columns)
        if missing:
            raise RuntimeError(f"{name} panel is missing columns: {sorted(missing)}")
        if frame[list(required - {"origin", "date"})].isna().any().any():
            raise RuntimeError(f"{name} panel contains missing values")
    return main, external


def panel_label(ax: plt.Axes, label: str, title: str) -> None:
    ax.set_title(f"{label}. {title}", loc="left", pad=8)


def save_figure(fig: plt.Figure, stem: str) -> None:
    fig.savefig(OUTPUT / f"{stem}.png", bbox_inches="tight", facecolor="white")
    fig.savefig(OUTPUT / f"{stem}.pdf", bbox_inches="tight", facecolor="white")
    fig.savefig(OUTPUT / f"{stem}.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def annotate_bars(ax: plt.Axes, bars, fmt: str = "{:.1f}", padding: float = 0.01) -> None:
    for bar in bars:
        width = float(bar.get_width())
        ax.text(
            width + padding,
            bar.get_y() + bar.get_height() / 2,
            fmt.format(width),
            va="center",
            ha="left",
            fontsize=7,
            color=COLORS["dark"],
        )


def draw_heatmap(
    ax: plt.Axes,
    values: np.ndarray,
    row_labels: list[str],
    col_labels: list[str],
    *,
    cmap: str,
    vmin: float | None = None,
    vmax: float | None = None,
    center: float | None = None,
    value_format: str = ".0f",
    colorbar_label: str = "",
    annotate: bool = True,
) -> None:
    if center is not None:
        bound = max(abs(float(np.nanmin(values) - center)), abs(float(np.nanmax(values) - center)))
        vmin = center - bound if vmin is None else vmin
        vmax = center + bound if vmax is None else vmax
    image = ax.imshow(values, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_xticks(np.arange(len(col_labels)), labels=col_labels)
    ax.set_yticks(np.arange(len(row_labels)), labels=row_labels)
    ax.tick_params(length=0)
    if annotate:
        midpoint = (
            (vmin if vmin is not None else np.nanmin(values))
            + (vmax if vmax is not None else np.nanmax(values))
        ) / 2
        for i in range(values.shape[0]):
            for j in range(values.shape[1]):
                value = values[i, j]
                if np.isnan(value):
                    continue
                if center is not None:
                    color = "white" if abs(value - center) > 0.52 * bound else COLORS["dark"]
                else:
                    color = "white" if value > midpoint else COLORS["dark"]
                ax.text(
                    j,
                    i,
                    format(value, value_format),
                    ha="center",
                    va="center",
                    fontsize=6.3,
                    color=color,
                )
    cbar = ax.figure.colorbar(image, ax=ax, fraction=0.046, pad=0.03)
    cbar.ax.tick_params(labelsize=7)
    if colorbar_label:
        cbar.set_label(colorbar_label, fontsize=8)


def make_figure2(main: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.3), gridspec_kw={"width_ratios": [1.35, 1]})
    absolute_errors = [np.abs(main[model] - main["true"]).to_numpy() for model in FOCAL_MODELS]
    positions = np.arange(1, len(FOCAL_MODELS) + 1)
    violin = axes[0].violinplot(
        absolute_errors,
        positions=positions,
        widths=0.72,
        showmeans=False,
        showmedians=False,
        showextrema=False,
        points=160,
    )
    for body, model in zip(violin["bodies"], FOCAL_MODELS, strict=False):
        body.set_facecolor(MODEL_COLORS[model])
        body.set_edgecolor("white")
        body.set_alpha(0.55)
    box = axes[0].boxplot(
        absolute_errors,
        positions=positions,
        widths=0.22,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": COLORS["dark"], "linewidth": 1.2},
        whiskerprops={"color": COLORS["dark"], "linewidth": 0.8},
        capprops={"color": COLORS["dark"], "linewidth": 0.8},
    )
    for patch, model in zip(box["boxes"], FOCAL_MODELS, strict=False):
        patch.set_facecolor(MODEL_COLORS[model])
        patch.set_alpha(0.9)
        patch.set_edgecolor(COLORS["dark"])
    cap = float(np.percentile(np.concatenate(absolute_errors), 99))
    axes[0].set_ylim(0, cap)
    axes[0].set_xticks(positions, [LABELS[m] for m in FOCAL_MODELS], rotation=18, ha="right")
    axes[0].set_ylabel("Absolute error (ILI percentage points)")
    axes[0].grid(axis="y")
    axes[0].text(
        0.99,
        0.98,
        "Display capped at pooled 99th percentile\n(all rows retained in statistics)",
        transform=axes[0].transAxes,
        ha="right",
        va="top",
        fontsize=7,
        color="#5E6873",
    )
    panel_label(axes[0], "A", "Paired absolute-error distributions")

    errors = pd.DataFrame({model: np.abs(main[model] - main["true"]) for model in FOCAL_MODELS})
    ranks = errors.rank(axis=1, method="min", ascending=True)
    categories = {
        "Best / tied best": ranks.eq(1).sum(axis=0),
        "Middle rank": ranks.isin([2, 3]).sum(axis=0),
        "Worst rank": ranks.eq(4).sum(axis=0),
    }
    left = np.zeros(len(FOCAL_MODELS), dtype=float)
    y = np.arange(len(FOCAL_MODELS))
    stack_colors = [COLORS["teal"], COLORS["orange"], COLORS["red"]]
    for (category, counts), color in zip(categories.items(), stack_colors, strict=False):
        values = counts.reindex(FOCAL_MODELS).to_numpy(dtype=float)
        bars = axes[1].barh(y, values, left=left, color=color, height=0.62, label=category)
        for bar, value in zip(bars, values, strict=False):
            if value >= 220:
                axes[1].text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{int(value):,}",
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=7,
                    fontweight="bold",
                )
        left += values
    axes[1].set_yticks(y, [LABELS[m] for m in FOCAL_MODELS])
    axes[1].invert_yaxis()
    axes[1].set_xlabel("Number of paired forecasts")
    axes[1].grid(axis="x")
    axes[1].legend(
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
    )
    panel_label(axes[1], "B", "Row-wise accuracy-rank counts")
    fig.suptitle(
        "Supplementary Figure 1. Main-test error distribution and accuracy-grade landscape "
        "(n = 4,968 paired forecasts)",
        fontsize=12,
        fontweight="bold",
        y=1.02,
    )
    fig.text(
        0.01,
        -0.02,
        "* KG horizon router uses DiseaseX at horizon 0 and paper AWBE at horizons 1-8; "
        "it is not an independent replacement.",
        fontsize=7.5,
        color="#5E6873",
    )
    fig.tight_layout()
    save_figure(fig, "figure2_main_error_distribution")


def bootstrap_wape_difference(
    part: pd.DataFrame, model: str, reference: str, seed: int
) -> tuple[float, float, float]:
    y = part["true"].to_numpy(dtype=float)
    model_error = np.abs(part[model].to_numpy(dtype=float) - y)
    reference_error = np.abs(part[reference].to_numpy(dtype=float) - y)
    point = float((model_error.sum() - reference_error.sum()) / np.abs(y).sum())
    rng = np.random.default_rng(seed)
    samples = 2500
    batch = 250
    estimates: list[np.ndarray] = []
    for _ in range(samples // batch):
        index = rng.integers(0, len(part), size=(batch, len(part)))
        numerator = (model_error[index] - reference_error[index]).sum(axis=1)
        denominator = np.abs(y[index]).sum(axis=1)
        estimates.append(numerator / denominator)
    distribution = np.concatenate(estimates)
    low, high = np.quantile(distribution, [0.025, 0.975])
    return point, float(low), float(high)


def temporal_i2(part: pd.DataFrame, model: str, reference: str) -> float:
    normalized = (
        np.abs(part[model] - part["true"]) - np.abs(part[reference] - part["true"])
    ) / max(float(np.abs(part["true"]).mean()), 1e-12)
    work = part.assign(normalized_effect=normalized, year=part["origin"].dt.year)
    effects: list[float] = []
    variances: list[float] = []
    for _, group in work.groupby("year"):
        if len(group) < 10:
            continue
        effects.append(float(group["normalized_effect"].mean()))
        variances.append(float(group["normalized_effect"].var(ddof=1) / len(group)))
    if len(effects) < 2:
        return 0.0
    effect = np.asarray(effects)
    variance = np.maximum(np.asarray(variances), 1e-8)
    weights = 1.0 / variance
    pooled = float(np.sum(weights * effect) / weights.sum())
    q = float(np.sum(weights * (effect - pooled) ** 2))
    degrees = len(effect) - 1
    return float(max(0.0, (q - degrees) / q) * 100.0) if q > 0 else 0.0


def make_figure3(main: pd.DataFrame) -> pd.DataFrame:
    effect_records: list[dict[str, float | int]] = []
    for horizon, part in main.groupby("week_ahead", sort=True):
        point, low, high = bootstrap_wape_difference(
            part, "KG_ADAPTIVE_ENSEMBLE", "AWBE", SEED + int(horizon)
        )
        effect_records.append(
            {
                "week_ahead": int(horizon),
                "n": len(part),
                "delta_wape": point,
                "ci_low": low,
                "ci_high": high,
                "i2": temporal_i2(part, "KG_ADAPTIVE_ENSEMBLE", "AWBE"),
            }
        )
    overall, overall_low, overall_high = bootstrap_wape_difference(
        main, "KG_ADAPTIVE_ENSEMBLE", "AWBE", SEED + 100
    )
    effects = pd.DataFrame(effect_records)
    effects.to_csv(OUTPUT / "figure3_diseasex_vs_awbe_effects.csv", index=False)

    fig = plt.figure(figsize=(15.2, 10.8))
    grid = fig.add_gridspec(
        2, 2, width_ratios=[1.65, 1], height_ratios=[1.25, 1], hspace=0.38, wspace=0.32
    )
    ax_a = fig.add_subplot(grid[0, 0])
    ax_b = fig.add_subplot(grid[0, 1])
    ax_c = fig.add_subplot(grid[1, 0])
    ax_d = fig.add_subplot(grid[1, 1])

    plot_rows = effects.sort_values("week_ahead", ascending=False).reset_index(drop=True)
    y = np.arange(len(plot_rows))
    point_pct = plot_rows["delta_wape"].to_numpy() * 100
    low_pct = plot_rows["ci_low"].to_numpy() * 100
    high_pct = plot_rows["ci_high"].to_numpy() * 100
    colors = [COLORS["teal"] if value < 0 else COLORS["orange"] for value in point_pct]
    ax_a.errorbar(
        point_pct,
        y,
        xerr=[point_pct - low_pct, high_pct - point_pct],
        fmt="none",
        ecolor="#6D7780",
        elinewidth=1.1,
        capsize=2.5,
        zorder=1,
    )
    ax_a.scatter(point_pct, y, c=colors, s=44, edgecolor="white", linewidth=0.7, zorder=2)
    ax_a.axvline(0, color=COLORS["dark"], linestyle="--", linewidth=0.9)
    ax_a.set_yticks(y, [f"Horizon {int(h)}" for h in plot_rows["week_ahead"]])
    ax_a.set_xlabel("Difference in WAPE (DiseaseX - AWBE), percentage points")
    ax_a.grid(axis="x")
    for yy, point, low, high in zip(y, point_pct, low_pct, high_pct, strict=False):
        ax_a.text(
            max(high_pct) + 0.8,
            yy,
            f"{point:+.1f} [{low:+.1f}, {high:+.1f}]",
            va="center",
            fontsize=7,
        )
    ax_a.set_xlim(min(low_pct.min() - 1.0, -2.0), max(high_pct.max() + 9.5, 8.0))
    ax_a.text(
        0.02,
        -0.18,
        (
            f"Overall: {overall * 100:+.1f} pp "
            f"(95% CI {overall_low * 100:+.1f} to {overall_high * 100:+.1f})"
        ),
        transform=ax_a.transAxes,
        fontsize=8,
        fontweight="bold",
    )
    panel_label(ax_a, "A", "Paired horizon-specific WAPE differences")

    widths = np.maximum((effects["ci_high"] - effects["ci_low"]).to_numpy(), 1e-8)
    precision = 1.0 / widths**2
    precision = precision / precision.max()
    order = effects.sort_values("week_ahead", ascending=False)
    bars = ax_b.barh(
        np.arange(len(order)),
        precision[order.index],
        color=COLORS["blue"],
        alpha=0.82,
        height=0.62,
    )
    ax_b.set_yticks(np.arange(len(order)), [f"H{h}" for h in order["week_ahead"]])
    ax_b.set_xlabel("Relative bootstrap precision")
    ax_b.set_xlim(0, 1.18)
    ax_b.grid(axis="x")
    for bar, n in zip(bars, order["n"], strict=False):
        ax_b.text(
            bar.get_width() + 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"n={int(n)}",
            va="center",
            fontsize=7,
        )
    panel_label(ax_b, "B", "Evidence weight and precision")

    i2_order = effects.sort_values("i2", ascending=True)
    colors_i2 = [COLORS["yellow"] if value < 50 else COLORS["red"] for value in i2_order["i2"]]
    bars = ax_c.barh(
        np.arange(len(i2_order)),
        i2_order["i2"],
        color=colors_i2,
        height=0.62,
    )
    ax_c.set_yticks(np.arange(len(i2_order)), [f"Horizon {h}" for h in i2_order["week_ahead"]])
    ax_c.axvline(50, color="#5E6873", linestyle="--", linewidth=0.8)
    ax_c.set_xlim(0, 105)
    ax_c.set_xlabel("Temporal heterogeneity, I² across calendar-year strata (%)")
    ax_c.grid(axis="x")
    annotate_bars(ax_c, bars, "{:.0f}%", 1.2)
    panel_label(ax_c, "C", "Between-year heterogeneity")

    focal_wape = {model: wape(main["true"], main[model]) * 100 for model in FOCAL_MODELS}
    matrix = np.asarray(
        [[focal_wape[row] - focal_wape[col] for col in FOCAL_MODELS] for row in FOCAL_MODELS]
    )
    bound = max(abs(matrix.min()), abs(matrix.max()))
    draw_heatmap(
        ax_d,
        matrix,
        [LABELS[m] for m in FOCAL_MODELS],
        [LABELS[m] for m in FOCAL_MODELS],
        cmap="RdBu_r",
        vmin=-bound,
        vmax=bound,
        center=0,
        value_format="+.1f",
        colorbar_label="Row - column WAPE (pp)",
    )
    ax_d.tick_params(axis="x", rotation=25)
    panel_label(ax_d, "D", "Pairwise WAPE differences")

    fig.suptitle(
        "Supplementary Figure 2. Paired evidence synthesis for DiseaseX versus paper AWBE",
        fontsize=13,
        fontweight="bold",
        y=0.98,
    )
    fig.text(
        0.01,
        0.01,
        "Negative differences favor DiseaseX. Confidence intervals use 2,500 paired "
        "bootstrap resamples. I² is estimated across calendar-year strata and describes "
        "temporal instability, not sampling quality.",
        fontsize=7.5,
        color="#5E6873",
    )
    save_figure(fig, "figure3_paired_effect_synthesis")
    return effects


def top_cells(metrics: pd.DataFrame, limit: int = 15) -> pd.DataFrame:
    return (
        metrics[metrics["week_ahead"].ge(0)]
        .sort_values(["wape", "model", "week_ahead"])
        .head(limit)
        .sort_values("wape", ascending=False)
        .reset_index(drop=True)
    )


def family_horizon_matrix(metrics: pd.DataFrame, bands: bool = False) -> pd.DataFrame:
    work = metrics[metrics["week_ahead"].ge(0)].copy()
    if bands:
        work["column"] = work["week_ahead"].map(horizon_band)
        columns = list(HORIZON_BANDS)
    else:
        work["column"] = work["week_ahead"].astype(int)
        columns = list(range(9))
    matrix = work.pivot_table(index="family", columns="column", values="wape", aggfunc="mean")
    return matrix.reindex(index=FAMILY_ORDER, columns=columns)


def make_figure4(main: pd.DataFrame, main_metrics: pd.DataFrame) -> None:
    fig = plt.figure(figsize=(18.2, 15.2))
    grid = fig.add_gridspec(3, 2, height_ratios=[1.05, 1.25, 1], hspace=0.42, wspace=0.28)
    axes = [fig.add_subplot(grid[i, j]) for i in range(3) for j in range(2)]
    ax_a, ax_b, ax_c, ax_d, ax_e, ax_f = axes

    top = top_cells(main_metrics)
    y = np.arange(len(top))
    colors = [FAMILY_COLORS[family(model)] for model in top["model"]]
    ax_a.hlines(y, 0, top["wape"] * 100, color=colors, alpha=0.7, linewidth=1.5)
    ax_a.scatter(top["wape"] * 100, y, c=colors, s=38, edgecolor="white", linewidth=0.6)
    ax_a.set_yticks(
        y, [f"{LABELS[m]} | h={h}" for m, h in zip(top["model"], top["week_ahead"], strict=False)]
    )
    ax_a.set_xlabel("WAPE (%) - lower is better")
    ax_a.grid(axis="x")
    panel_label(ax_a, "A", "Top-15 model-horizon combinations")

    model_order = (
        main_metrics[main_metrics["week_ahead"].eq(-1)].sort_values("wape")["model"].tolist()
    )
    heat = (
        main_metrics[main_metrics["week_ahead"].ge(0)]
        .pivot(index="model", columns="week_ahead", values="wape")
        .reindex(index=model_order, columns=range(9))
        * 100
    )
    draw_heatmap(
        ax_b,
        heat.to_numpy(),
        [LABELS[m] for m in heat.index],
        [str(h) for h in heat.columns],
        cmap="YlGnBu_r",
        value_format=".0f",
        colorbar_label="WAPE (%)",
        annotate=True,
    )
    ax_b.set_xlabel("Forecast horizon (weeks)")
    panel_label(ax_b, "B", "Model-horizon WAPE landscape")

    cells = main_metrics[main_metrics["week_ahead"].ge(0)].copy()
    family_summary = cells.groupby("family", sort=False).agg(
        cells=("wape", "size"), mean_wape=("wape", "mean")
    )
    family_summary = family_summary.reindex(FAMILY_ORDER)
    bars = ax_c.bar(
        np.arange(len(FAMILY_ORDER)),
        family_summary["cells"],
        color=[FAMILY_COLORS[name] for name in FAMILY_ORDER],
        width=0.68,
    )
    ax_c.set_xticks(np.arange(len(FAMILY_ORDER)), FAMILY_ORDER, rotation=20, ha="right")
    ax_c.set_ylabel("Number of model-horizon cells")
    ax_c.grid(axis="y")
    line_ax = ax_c.twinx()
    line_ax.plot(
        np.arange(len(FAMILY_ORDER)),
        family_summary["mean_wape"] * 100,
        color=COLORS["dark"],
        marker="o",
        linewidth=1.2,
        markersize=4,
    )
    line_ax.set_ylabel("Mean WAPE across cells (%)")
    line_ax.spines["top"].set_visible(False)
    for bar, value in zip(bars, family_summary["cells"], strict=False):
        ax_c.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1,
            str(int(value)),
            ha="center",
            fontsize=7,
        )
    panel_label(ax_c, "C", "Composition by model family")

    standalone = cells[cells["model"].ne("KG_HORIZON_ROUTER")]
    winner_rows = standalone.loc[standalone.groupby("week_ahead")["wape"].idxmin()]
    winner_counts = winner_rows["model"].value_counts().sort_values()
    bars = ax_d.barh(
        np.arange(len(winner_counts)),
        winner_counts.to_numpy(),
        color=[
            MODEL_COLORS.get(model, FAMILY_COLORS[family(model)]) for model in winner_counts.index
        ],
        height=0.62,
    )
    ax_d.set_yticks(np.arange(len(winner_counts)), [LABELS[m] for m in winner_counts.index])
    ax_d.set_xlabel("Number of horizons with lowest standalone WAPE")
    ax_d.set_xlim(0, max(winner_counts.max() + 1.2, 3))
    ax_d.grid(axis="x")
    annotate_bars(ax_d, bars, "{:.0f}", 0.12)
    inset = ax_d.inset_axes([0.66, 0.50, 0.31, 0.44])
    inset.pie(
        winner_counts.to_numpy(),
        colors=[
            MODEL_COLORS.get(model, FAMILY_COLORS[family(model)]) for model in winner_counts.index
        ],
        startangle=90,
        wedgeprops={"width": 0.38, "edgecolor": "white"},
    )
    inset.text(0, 0, "9\nhorizons", ha="center", va="center", fontsize=8, fontweight="bold")
    panel_label(ax_d, "D", "Best standalone method by horizon")

    family_heat = family_horizon_matrix(main_metrics) * 100
    draw_heatmap(
        ax_e,
        family_heat.to_numpy(),
        FAMILY_ORDER,
        [str(h) for h in family_heat.columns],
        cmap="YlGnBu_r",
        value_format=".0f",
        colorbar_label="Mean WAPE (%)",
    )
    ax_e.set_xlabel("Forecast horizon (weeks)")
    panel_label(ax_e, "E", "Family x horizon cross-tabulation")

    band_records: list[dict[str, float | str]] = []
    for band, horizons in HORIZON_BANDS.items():
        part = cells[cells["week_ahead"].isin(horizons)]
        for family_name in FAMILY_ORDER:
            values = part.loc[part["family"].eq(family_name), "wape"]
            band_records.append(
                {"band": band, "family": family_name, "mean_wape": float(values.mean()) * 100}
            )
    band_frame = pd.DataFrame(band_records)
    x = np.arange(len(HORIZON_BANDS))
    width = 0.15
    for offset, family_name in enumerate(FAMILY_ORDER):
        values = (
            band_frame[band_frame["family"].eq(family_name)]
            .set_index("band")
            .reindex(HORIZON_BANDS)["mean_wape"]
            .to_numpy()
        )
        ax_f.bar(
            x + (offset - 2) * width,
            values,
            width=width,
            color=FAMILY_COLORS[family_name],
            label=family_name,
        )
    ax_f.set_xticks(x, list(HORIZON_BANDS), rotation=15, ha="right")
    ax_f.set_ylabel("Mean WAPE (%)")
    ax_f.grid(axis="y")
    ax_f.legend(ncol=2, frameon=False, loc="upper left")
    panel_label(ax_f, "F", "Forecast-band performance by family")

    fig.suptitle(
        "Supplementary Figure 3. Main-test model-horizon performance landscape "
        "(4,968 paired forecasts)",
        fontsize=13,
        fontweight="bold",
        y=0.995,
    )
    fig.text(
        0.01,
        0.006,
        "WAPE is the primary point-forecast metric. The orchestration family contains the "
        "KG horizon router, which retains paper AWBE for horizons 1-8 and is therefore "
        "shown separately from standalone models.",
        fontsize=7.5,
        color="#5E6873",
    )
    save_figure(fig, "figure4_main_model_horizon_landscape")


def make_figure5(external: pd.DataFrame, external_metrics: pd.DataFrame) -> None:
    fig = plt.figure(figsize=(18.2, 15.2))
    grid = fig.add_gridspec(3, 2, height_ratios=[1.05, 1.15, 1], hspace=0.42, wspace=0.28)
    axes = [fig.add_subplot(grid[i, j]) for i in range(3) for j in range(2)]
    ax_a, ax_b, ax_c, ax_d, ax_e, ax_f = axes

    top = top_cells(external_metrics)
    y = np.arange(len(top))
    colors = [FAMILY_COLORS[family(model)] for model in top["model"]]
    ax_a.hlines(y, 0, top["wape"] * 100, color=colors, alpha=0.7, linewidth=1.5)
    ax_a.scatter(top["wape"] * 100, y, c=colors, s=38, edgecolor="white", linewidth=0.6)
    ax_a.set_yticks(
        y, [f"{LABELS[m]} | h={h}" for m, h in zip(top["model"], top["week_ahead"], strict=False)]
    )
    ax_a.set_xlabel("WAPE (%) - lower is better")
    ax_a.grid(axis="x")
    panel_label(ax_a, "A", "Top-15 post-COVID model-horizon combinations")

    cells = external_metrics[external_metrics["week_ahead"].ge(0)].copy()
    standalone = cells[cells["model"].ne("KG_HORIZON_ROUTER")]
    winner_rows = standalone.loc[standalone.groupby("week_ahead")["wape"].idxmin()]
    winner_counts = winner_rows["model"].value_counts().sort_values()
    bars = ax_b.barh(
        np.arange(len(winner_counts)),
        winner_counts.to_numpy(),
        color=[
            MODEL_COLORS.get(model, FAMILY_COLORS[family(model)]) for model in winner_counts.index
        ],
        height=0.62,
    )
    ax_b.set_yticks(np.arange(len(winner_counts)), [LABELS[m] for m in winner_counts.index])
    ax_b.set_xlabel("Number of horizons with lowest standalone WAPE")
    ax_b.set_xlim(0, max(winner_counts.max() + 1.3, 3))
    ax_b.grid(axis="x")
    annotate_bars(ax_b, bars, "{:.0f}", 0.12)
    panel_label(ax_b, "B", "Standalone winner composition")

    band_counts = {
        band: int(external["week_ahead"].isin(horizons).sum())
        for band, horizons in HORIZON_BANDS.items()
    }
    wedges, _ = ax_c.pie(
        list(band_counts.values()),
        colors=[COLORS["blue"], COLORS["teal"], COLORS["orange"], COLORS["purple"]],
        startangle=90,
        wedgeprops={"width": 0.36, "edgecolor": "white"},
    )
    ax_c.text(0, 0, f"{len(external)}\npaired rows", ha="center", va="center", fontweight="bold")
    ax_c.legend(
        wedges,
        [f"{name}: {value}" for name, value in band_counts.items()],
        loc="center left",
        bbox_to_anchor=(0.98, 0.5),
        frameon=False,
    )
    panel_label(ax_c, "C", "Evaluation rows by forecast band")

    family_band = family_horizon_matrix(external_metrics, bands=True) * 100
    draw_heatmap(
        ax_d,
        family_band.to_numpy(),
        FAMILY_ORDER,
        list(HORIZON_BANDS),
        cmap="YlGnBu_r",
        value_format=".0f",
        colorbar_label="Mean WAPE (%)",
    )
    ax_d.tick_params(axis="x", rotation=18)
    panel_label(ax_d, "D", "Family x forecast-band WAPE")

    disease_x_error = np.abs(external["KG_ADAPTIVE_ENSEMBLE"] - external["true"])
    violin_data = [
        disease_x_error[external["week_ahead"].isin(horizons)].to_numpy()
        for horizons in HORIZON_BANDS.values()
    ]
    positions = np.arange(1, len(HORIZON_BANDS) + 1)
    violins = ax_e.violinplot(
        violin_data,
        positions=positions,
        widths=0.75,
        showmeans=False,
        showextrema=False,
        showmedians=True,
        points=120,
    )
    for body, color in zip(
        violins["bodies"],
        [COLORS["blue"], COLORS["teal"], COLORS["orange"], COLORS["purple"]],
        strict=False,
    ):
        body.set_facecolor(color)
        body.set_edgecolor("white")
        body.set_alpha(0.62)
    violins["cmedians"].set_color(COLORS["dark"])
    ax_e.set_xticks(positions, list(HORIZON_BANDS), rotation=15, ha="right")
    ax_e.set_ylabel("DiseaseX absolute error (ILI percentage points)")
    ax_e.grid(axis="y")
    panel_label(ax_e, "E", "DiseaseX error distribution by forecast band")

    comparison_models = ["AWAE", "AWBE", "KG_ADAPTIVE_ENSEMBLE", "KG_HORIZON_ROUTER"]
    base_positions = np.arange(len(HORIZON_BANDS))
    bar_height = 0.18
    model_offsets = np.linspace(-0.28, 0.28, len(comparison_models))
    for model, offset in zip(comparison_models, model_offsets, strict=False):
        improvements: list[float] = []
        for horizons in HORIZON_BANDS.values():
            part = external[external["week_ahead"].isin(horizons)]
            baseline = wape(part["true"], part["Baseline"])
            candidate = wape(part["true"], part[model])
            improvements.append((1.0 - candidate / baseline) * 100)
        ax_f.barh(
            base_positions + offset,
            improvements,
            height=bar_height,
            color=MODEL_COLORS.get(model, FAMILY_COLORS[family(model)]),
            label=LABELS[model],
        )
    ax_f.axvline(0, color=COLORS["dark"], linewidth=0.8)
    ax_f.set_yticks(base_positions, list(HORIZON_BANDS))
    ax_f.invert_yaxis()
    ax_f.set_xlabel("WAPE improvement versus constant baseline (%)")
    ax_f.grid(axis="x")
    ax_f.legend(
        frameon=False,
        ncol=1,
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
    )
    panel_label(ax_f, "F", "External relative-accuracy profile")

    fig.suptitle(
        "Supplementary Figure 4. Post-COVID external generalization landscape "
        "(2023-03-05 to 2024-01-21)",
        fontsize=13,
        fontweight="bold",
        y=0.995,
    )
    fig.text(
        0.01,
        0.006,
        "All 423 rows use the configuration locked before external evaluation. WAPE "
        "improvement is relative to the constant baseline. The KG horizon router is "
        "paper-dependent and must not be interpreted as an independent DiseaseX model.",
        fontsize=7.5,
        color="#5E6873",
    )
    save_figure(fig, "figure5_postcovid_generalization_landscape")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    configure_style()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    main_panel, external_panel = load_data()
    main_metrics = metric_panel(main_panel, ALL_MODELS)
    external_metrics = metric_panel(external_panel, ALL_MODELS)
    main_metrics.to_csv(OUTPUT / "main_metrics_recomputed.csv", index=False)
    external_metrics.to_csv(OUTPUT / "postcovid_metrics_recomputed.csv", index=False)

    make_figure2(main_panel)
    effects = make_figure3(main_panel)
    make_figure4(main_panel, main_metrics)
    make_figure5(external_panel, external_metrics)

    summary = {
        "status": "COMPLETE",
        "primary_metric": "WAPE",
        "main_rows": len(main_panel),
        "main_origins": int(main_panel["origin"].nunique()),
        "external_rows": len(external_panel),
        "external_origins": int(external_panel["origin"].nunique()),
        "models": ALL_MODELS,
        "figures": [
            "figure2_main_error_distribution",
            "figure3_paired_effect_synthesis",
            "figure4_main_model_horizon_landscape",
            "figure5_postcovid_generalization_landscape",
        ],
        "overall_wape": {
            model: wape(main_panel["true"], main_panel[model]) for model in FOCAL_MODELS
        },
        "external_wape": {
            model: wape(external_panel["true"], external_panel[model]) for model in FOCAL_MODELS
        },
        "diseasex_vs_awbe_delta_wape_by_horizon": effects.set_index("week_ahead")[
            "delta_wape"
        ].to_dict(),
        "limitations": [
            "The KG horizon router uses DiseaseX only at horizon 0 and paper AWBE at horizons 1-8.",
            (
                "The figures reproduce the reference article's visual grammar, not its "
                "EEG-specific estimands."
            ),
            (
                "WAPE is emphasized as the primary point-forecast metric; RMSE is retained "
                "only in audit tables."
            ),
        ],
    }
    (OUTPUT / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = []
    for path in sorted(OUTPUT.iterdir()):
        if path.name == "MANIFEST.sha256" or not path.is_file():
            continue
        manifest.append(f"{sha256(path)}  {path.name}")
    (OUTPUT / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

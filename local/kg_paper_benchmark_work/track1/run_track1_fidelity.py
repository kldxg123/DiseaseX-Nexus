"""Track 1: fidelity of the Python method implementations (src/benchlib.py) against the
official artifacts of Bleichrodt et al. 2024 (R. Soc. Open Sci. 11(7):240248).

What this script does, in order:

1. Version provenance: sha256 of the official data/code/forecast artifacts, the
   zip comment commit of the downloaded GitHub snapshot, and the Zenodo record
   11504600 facts collected online (recorded statically with retrieval date).
2. Cleaning reproduction: pandas re-implementation of the official
   Code/Data-Cleaning-and-Preparation.R pipeline (Thursday-start weeks via
   lubridate floor_date(week_start = 4), location selection, OWID+CDC merge,
   NA -> 0, drop of week 2022-04-28), exported as weekly CSVs with sha256.
3. Cleaning verification: every official n-sub-epidemic ranked(1) horizon-1
   calibration-10 forecast file and every official ARIMA Formatted horizon-1
   calibration-10 file embeds its 10-week calibration window; we check the
   embedded observed values against the reconstructed series (all 9 locations,
   all 30 origins).
4. Forecast fidelity: for 18 origins (6 per location x {World, Brazil,
   US(OWID)}, early/mid/late) and horizons 1..4, run benchlib's
   forecast_arima_window / forecast_slr_window / forecast_subepidemic_window on
   the same 10-week calibration window the official files used, and compare
   point forecasts against the official All-Quantiles / ranked(1) CSVs.
   A supplementary run does the same on the 11-week calibration window (the
   paper's primary calibration) for a 9-origin subset.
5. Paper-table check: recompute France calibration-11 mean MAE by model and
   horizon from Additional Metrics/mpox-metrics-forecast-periods.xlsx and
   compare with the numbers quoted in the paper text.

Outputs (all under kg_paper_benchmark_work/track1/):
  track1_fidelity.json                machine-readable results and verdicts
  track1_reconstructed_series/*.csv   weekly series per location
  track1_reconstructed_series/SHA256SUMS.txt

Fidelity verdicts: ARIMA/SLR pass when median relative error < 5%;
sub-epidemic passes when median relative error < 20% (optimizer differences
between MATLAB fmincon-style fits and scipy least_squares motivate the wider
band).  Failures are reported, not hidden.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("F:/作业/aibola/kg_paper_benchmark_work")
_REPO_DIR = (
    "Evaluating-the-forecasting-performance-of-ensemble-"
    "sub-epidemic-frameworks-and-other-time-series-main"
)
OFFICIAL = REPO / "official_code_extract" / _REPO_DIR
SRC = REPO / "src"
OUT = REPO / "track1"
SERIES_DIR = OUT / "track1_reconstructed_series"

sys.path.insert(0, str(SRC))
import benchlib  # noqa: E402

# --- constants ----------------------------------------------------------------
OWID_LOCATIONS = [
    "United States",
    "Brazil",
    "Spain",
    "France",
    "United Kingdom",
    "Germany",
    "Canada",
    "World",
]
# Official output columns (R spread() alphabetical order of country labels).
ALL_COUNTRIES = [
    "Brazil",
    "Canada",
    "France",
    "Germany",
    "Spain",
    "US(CDC)",
    "US(OWID)",
    "United Kingdom",
    "World",
]
# n-sub-epidemic zip entries replace "(" "/" ")" with "-".
NSUB_NAME = {"US(OWID)": "US-OWID", "US(CDC)": "US-CDC", "United Kingdom": "United-Kingdom"}

COMPARE_LOCATIONS = ["World", "Brazil", "US(OWID)"]
# early / mid / late origins inside the official calibration-10 origin range
# 2022-07-07 .. 2023-01-26 (30 weekly origins).  The official n-sub-epidemic
# artifacts omit the first three Brazil origins (low case counts, acknowledged
# in the paper), so Brazil starts one origin later to keep the origin set
# identical across the three compared models.
ORIGINS = ["07-21-2022", "08-04-2022", "10-06-2022", "11-10-2022", "12-29-2022", "01-26-2023"]
ORIGINS_BY_LOCATION = {
    "World": ORIGINS,
    "US(OWID)": ORIGINS,
    "Brazil": ["07-28-2022", "08-04-2022", "10-06-2022", "11-10-2022", "12-29-2022", "01-26-2023"],
}
SUPP_11W_ORIGINS = ["08-04-2022", "11-10-2022", "01-26-2023"]
HORIZONS = (1, 2, 3, 4)

TOLERANCE = {"ARIMA": 0.05, "SLR": 0.05, "SE-Ranked(1)": 0.20}

HASHED_FILES = [
    "Orignal_Data/owid-monkeypox-data.csv",
    "Orignal_Data/CDC-08-09-2023.csv",
    "Code/Data-Cleaning-and-Preparation.R",
    "Code/Forecasting-and-Observed-Data.R",
    "Code/Sensitivity-Analysis.R",
    "Code/Calculating-Average-Metrics.Rmd",
    "Code/Merging-Forecast-Period-Metrics.Rmd",
    "Code/Skill-Scores.Rmd",
    "Code/Winkler Scores.Rmd",
    "Code/Mpox_Metrics.m",
    "Forecasts/GAM.zip",
    "Forecasts/Prophet.zip",
    "Forecasts/SLR.zip",
    "Forecasts/Spatial-wave.zip",
    "Forecasts/n-sub-epidemic.zip",
    "Additional Metrics/All-Winkler-Scores.xlsx",
    "Additional Metrics/mpox-metrics-forecast-periods.xlsx",
    "README.md",
    "LICENSE",
]

# Retrieved 2026-08-19 from https://zenodo.org/api/records/11504600 and the
# GitHub API (refs/tags/v1.0.0, commit 1222ec8).  Recorded statically so the
# run does not depend on network access.
ZENODO_FACTS = {
    "record_id": 11504600,
    "doi": "10.5281/zenodo.11504600",
    "version": "v1.0.0",
    "publication_date": "2024-06-06",
    "github_tag_commit": "fe726df742d2bd3effb76040d6c4b15513fe1079",
    "zenodo_zip_key": (
        "bleicham/Evaluating-the-forecasting-performance-of-ensemble-"
        "sub-epidemic-frameworks-and-other-time-series-v1.0.0.zip"
    ),
    "zenodo_zip_size_bytes": 25001865,
    "zenodo_zip_md5": "300aee96e4080da1e9fa452988452956",
    "retrieved": "2026-08-19",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- cleaning reproduction ----------------------------------------------------
def reconstruct_weekly() -> pd.DataFrame:
    """pandas re-implementation of Code/Data-Cleaning-and-Preparation.R."""
    owid = pd.read_csv(OFFICIAL / "Orignal_Data" / "owid-monkeypox-data.csv", parse_dates=["date"])
    owid = owid[owid["date"] <= pd.Timestamp("2023-08-09")]
    owid = owid[owid["location"].isin(OWID_LOCATIONS)][["date", "location", "new_cases"]].copy()
    owid["Country"] = owid["location"].where(owid["location"] != "United States", "US(OWID)")
    owid = owid.rename(columns={"date": "Day", "new_cases": "Cases"})[["Day", "Country", "Cases"]]

    cdc = pd.read_csv(OFFICIAL / "Orignal_Data" / "CDC-08-09-2023.csv")
    cdc_day = pd.to_datetime(cdc["Epi_date_V3"], format="%m/%d/%Y")
    cdc_cases = pd.DataFrame({"Day": cdc_day, "Country": "US(CDC)", "Cases": cdc["Cases"]})

    case_data = pd.concat([owid, cdc_cases], ignore_index=True)
    # lubridate::floor_date(unit = "week", week_start = 4): Thursday-start weeks.
    weekday = case_data["Day"].dt.weekday  # Monday = 0
    case_data["Week"] = case_data["Day"] - pd.to_timedelta((weekday - 3) % 7, unit="D")
    weekly = case_data.groupby(["Week", "Country"], as_index=False)["Cases"].sum()
    wide = weekly.pivot(index="Week", columns="Country", values="Cases")
    wide = wide.reindex(columns=ALL_COUNTRIES)
    wide = wide[wide.index != pd.Timestamp("2022-04-28")]
    wide = wide.fillna(0.0).sort_index()
    return wide


def origin_to_date(label: str) -> pd.Timestamp:
    return pd.Timestamp(datetime.strptime(label, "%m-%d-%Y").date())


def date_to_label(d: date | pd.Timestamp) -> str:
    ts = pd.Timestamp(d)
    return ts.strftime("%m-%d-%Y")


def calibration_window(
    wide: pd.DataFrame, location: str, origin: pd.Timestamp, n: int
) -> tuple[float, ...]:
    """The n weekly values of `location` ending at `origin` (origin inclusive)."""
    weeks = pd.date_range(end=origin, periods=n, freq="7D")
    if not all(w in wide.index for w in weeks):
        raise KeyError(f"window weeks missing for {location} ending {origin.date()}")
    return tuple(float(v) for v in wide.loc[weeks, location])


# --- official forecast readers --------------------------------------------------
def read_official_arima(calibration: int, horizon: int, location: str, origin_label: str) -> float:
    path = (
        OFFICIAL
        / "Forecasts"
        / "ARIMA"
        / "Forecasts"
        / "All-Quantiles"
        / f"ARIMA-weekly-Mpox-Cases-calibration-{calibration}-horizon-{horizon}"
        f"-{location}-{origin_label}.csv"
    )
    df = pd.read_csv(path)
    means = df["means"].to_numpy(dtype=float)
    if len(means) != horizon:
        raise ValueError(f"{path.name}: expected {horizon} rows, got {len(means)}")
    return float(means[-1])


def read_official_from_zip(
    zip_name: str, entry: str, horizon: int, column: str
) -> tuple[float, pd.DataFrame]:
    with zipfile.ZipFile(OFFICIAL / "Forecasts" / zip_name) as zf:
        with zf.open(entry) as fh:
            df = pd.read_csv(io.BytesIO(fh.read()))
    values = df[column].to_numpy(dtype=float)
    if len(values) < horizon:
        raise ValueError(f"{entry}: expected at least {horizon} rows, got {len(values)}")
    return float(values[horizon - 1] if column == "means" else values[-1]), df


def read_official_slr(calibration: int, horizon: int, location: str, origin_label: str) -> float:
    entry = (
        f"SLR/Forecasts/All-Quantiles/SLR-weekly-Mpox-Cases-calibration-{calibration}"
        f"-horizon-{horizon}-{location}-{origin_label}.csv"
    )
    value, _df = read_official_from_zip("SLR.zip", entry, horizon, "means")
    return value


def read_official_nsub(
    calibration: int, horizon: int, location: str, origin_label: str
) -> tuple[float, pd.DataFrame]:
    loc = NSUB_NAME.get(location, location)
    entry = (
        f"n-sub-epidemic/Calibration-{calibration}-Weeks/"
        f"ranked(1)-onsetfixed-0-flag1-1-method-0-dist-0-horizon-{horizon}"
        f"-{loc}-{origin_label}.csv"
    )
    return read_official_from_zip("n-sub-epidemic.zip", entry, horizon, "median")


# --- cleaning verification ------------------------------------------------------
def verify_cleaning_against_official(wide: pd.DataFrame) -> dict:
    """Check embedded calibration windows of every official horizon-1 file.

    n-sub-epidemic ranked(1) files carry the observed series in column `data`
    for the calibration rows; ARIMA Formatted files carry the same in `data`.
    """
    result: dict[str, dict[str, int]] = {}
    # n-sub-epidemic zip: ranked(1), all locations x origins, calibration-10,
    # horizon-1.  The toolbox zero-truncates the calibration window, so the
    # file's `data` rows do not necessarily span the last 10 weeks; compare
    # date-keyed instead.  Forecast rows also carry the observed future values
    # (embedded post-hoc), so every row is checkable against the reconstruction.
    checked = mismatched = 0
    rows_checked = 0
    per_location: dict[str, dict[str, int]] = {}
    cross_match = 0  # mismatched US files whose data matches the OTHER US series
    mismatch_examples: list[str] = []
    with zipfile.ZipFile(OFFICIAL / "Forecasts" / "n-sub-epidemic.zip") as zf:
        entries = [
            n
            for n in zf.namelist()
            if n.startswith("n-sub-epidemic/Calibration-10-Weeks/ranked(1)-")
            and "horizon-1-" in n
            and n.endswith(".csv")
        ]
        inv_nsub = {v: k for k, v in NSUB_NAME.items()}
        for entry in entries:
            stem = entry.rsplit("/", 1)[-1]
            tail = stem.split("-dist-0-horizon-1-")[-1][: -len(".csv")]
            loc_dashed = tail.rsplit("-", 3)[0]
            location = inv_nsub.get(loc_dashed, loc_dashed.replace("-", " "))
            with zf.open(entry) as fh:
                df = pd.read_csv(io.BytesIO(fh.read()))
            bad = False
            cross_bad = False
            other = "US(CDC)" if location == "US(OWID)" else "US(OWID)"
            for row in df.itertuples(index=False):
                week = pd.Timestamp(date(int(row.year), int(row.month), int(row.day)))
                rows_checked += 1
                if not np.isclose(float(row.data), float(wide.loc[week, location]), atol=1e-9):
                    bad = True
                    if location.startswith("US(") and not np.isclose(
                        float(row.data), float(wide.loc[week, other]), atol=1e-9
                    ):
                        cross_bad = True
            checked += 1
            slot = per_location.setdefault(location, {"checked": 0, "mismatched": 0})
            slot["checked"] += 1
            if bad:
                mismatched += 1
                slot["mismatched"] += 1
                if location.startswith("US(") and not cross_bad:
                    cross_match += 1
                if len(mismatch_examples) < 5:
                    mismatch_examples.append(entry)
    result["nsub_ranked1_calibration10_h1"] = {
        "checked": checked,
        "mismatched": mismatched,
        "rows_checked": rows_checked,
        "per_location": per_location,
        "us_label_swap_cross_matches": cross_match,
    }

    # ARIMA Formatted files on disk: calibration rows carry data == observed.
    checked2 = mismatched2 = 0
    fmt_dir = OFFICIAL / "Forecasts" / "ARIMA" / "Forecasts" / "Formatted"
    for path in sorted(fmt_dir.glob("ARIMA-*-calibration-10-horizon-1-*.csv")):
        stem = path.name[: -len("-Formatted.csv")]
        tail = stem.split("-horizon-1-")[-1]
        location, origin_label = tail.rsplit("-", 3)[0], "-".join(tail.rsplit("-", 3)[1:])
        df = pd.read_csv(path)
        obs = df["data"].to_numpy(dtype=float)[:10]
        window = calibration_window(wide, location, origin_to_date(origin_label), 10)
        checked2 += 1
        if not np.allclose(obs, np.asarray(window), atol=1e-9):
            mismatched2 += 1
            if len(mismatch_examples) < 10:
                mismatch_examples.append(path.name)
    result["arima_formatted_calibration10_h1"] = {"checked": checked2, "mismatched": mismatched2}
    result["mismatch_examples"] = mismatch_examples  # type: ignore[assignment]
    return result


# --- fidelity comparison --------------------------------------------------------
def ours_forecasts(model: str, window: tuple[float, ...]) -> dict[int, float]:
    if model == "SLR":
        fc = benchlib.forecast_slr_window(window)
    elif model == "ARIMA":
        fc = benchlib.forecast_arima_window(window, benchlib.ArimaStats())
    elif model == "SE-Ranked(1)":
        fc = benchlib.forecast_subepidemic_window(window, benchlib.SubepidemicStats())
    else:
        raise ValueError(model)
    return {h: float(fc[h].yhat) for h in HORIZONS}


def official_forecast(model: str, calibration: int, h: int, location: str, origin: str) -> float:
    if model == "SLR":
        return read_official_slr(calibration, h, location, origin)
    if model == "ARIMA":
        return read_official_arima(calibration, h, location, origin)
    if model == "SE-Ranked(1)":
        value, _df = read_official_nsub(calibration, h, location, origin)
        return value
    raise ValueError(model)


def official_forecast_or_nan(
    model: str, calibration: int, h: int, location: str, origin: str
) -> float | None:
    """Official point forecast, or None when the official artifact is missing."""
    try:
        return official_forecast(model, calibration, h, location, origin)
    except (KeyError, FileNotFoundError):
        return None


def run_comparison(
    wide: pd.DataFrame, calibration: int, origins: list[str]
) -> dict[str, dict]:
    models = ["SLR", "ARIMA", "SE-Ranked(1)"]
    per_model: dict[str, list[dict]] = {m: [] for m in models}
    missing: dict[str, int] = {m: 0 for m in models}
    for location in COMPARE_LOCATIONS:
        for origin in ORIGINS_BY_LOCATION[location] if calibration == 10 else origins:
            origin_date = origin_to_date(origin)
            for model in models:
                # The official MATLAB toolbox artifacts swapped the two US
                # series (see discrepancy D6): the "US-OWID" ranked(1) files
                # were fit to US(CDC) calibration data.  For a like-for-like
                # input comparison the SE model is therefore run on the series
                # the official artifact actually used.
                window_location = location
                note = None
                if model == "SE-Ranked(1)" and location == "US(OWID)":
                    window_location = "US(CDC)"
                    note = (
                        "input-aligned: official US-OWID artifact was fit to US(CDC) "
                        "data (label swap, discrepancy D6)"
                    )
                window = calibration_window(wide, window_location, origin_date, calibration)
                ours = ours_forecasts(model, window)
                for h in HORIZONS:
                    official = official_forecast_or_nan(model, calibration, h, location, origin)
                    if official is None:
                        missing[model] += 1
                        continue
                    abs_diff = abs(ours[h] - official)
                    rel_diff = abs_diff / max(abs(official), 1.0)
                    point = {
                        "location": location,
                        "origin": origin,
                        "horizon": h,
                        "official": official,
                        "ours": ours[h],
                        "abs_diff": abs_diff,
                        "rel_diff": rel_diff,
                    }
                    if note is not None:
                        point["note"] = note
                    per_model[model].append(point)
    summary: dict[str, dict] = {}
    for model in models:
        rows = per_model[model]
        rel = np.asarray([r["rel_diff"] for r in rows])
        abs_a = np.asarray([r["abs_diff"] for r in rows])
        median_rel = float(np.median(rel))
        tol = TOLERANCE[model]
        summary[model] = {
            "calibration_weeks": calibration,
            "n_points": len(rows),
            "n_missing_official_points": missing[model],
            "median_rel_error": median_rel,
            "max_rel_error": float(rel.max()),
            "mean_abs_error_vs_official": float(abs_a.mean()),
            "tolerance_median_rel": tol,
            "verdict": "pass" if median_rel < tol else "fail",
            "points": rows,
        }
    return summary


# --- paper-table check ----------------------------------------------------------
def paper_table_checks() -> list[dict]:
    metrics = pd.read_excel(OFFICIAL / "Additional Metrics" / "mpox-metrics-forecast-periods.xlsx")
    fr = metrics[(metrics["Location"] == "France") & (metrics["Calibration"] == 11)]
    mae = fr.groupby(["Model", "Horizon"])["Mean Absolute Error (MAE)"].mean()
    arima = {h: round(float(mae[("ARIMA", h)]), 1) for h in HORIZONS}
    se1_h1 = round(float(mae[("SE-Ranked(1)", 1)]), 1)
    # Paper (results, France paragraph): "the ARIMA model performed best across
    # most forecasting horizons (range: 90.5-93.8), albeit the one-week horizon,
    # where the n-sub-epidemic top-ranked (80.9) ... performed best".
    recomputed_arima_best_range = (arima[2], arima[4])
    return [
        {
            "check": "France calibration-11 ARIMA mean MAE, horizons 2-4 (paper: 90.5-93.8)",
            "recomputed": [arima[h] for h in HORIZONS],
            "paper_range_h2_h4": [90.5, 93.8],
            "match": recomputed_arima_best_range == (90.5, 93.8),
        },
        {
            "check": "France calibration-11 SE-Ranked(1) mean MAE at horizon 1 (paper: 80.9)",
            "recomputed": se1_h1,
            "paper": 80.9,
            "match": se1_h1 == 80.9,
        },
    ]


# --- main -----------------------------------------------------------------------
def main() -> None:
    OUT.mkdir(exist_ok=True)
    SERIES_DIR.mkdir(exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    hashes = {rel: sha256_file(OFFICIAL / rel) for rel in HASHED_FILES}
    local_zip = REPO / "official_code.zip"
    zip_comment = ""
    with zipfile.ZipFile(local_zip) as zf:
        zip_comment = zf.comment.decode()
    version_provenance = {
        "local_snapshot_zip": {
            "path": str(local_zip),
            "size_bytes": local_zip.stat().st_size,
            "md5": hashlib.md5(local_zip.read_bytes()).hexdigest(),  # noqa: S324
            "sha256": sha256_file(local_zip),
            "zip_comment_commit": zip_comment,
            "commit_message": "Create LICENSE (2024-06-06T12:58:45Z, GitHub main head at download)",
        },
        "zenodo_11504600": ZENODO_FACTS,
        "note": (
            "The local extract is the GitHub main snapshot at commit " + zip_comment + ", not the "
            "Zenodo v1.0.0 archive (tag commit fe726df742d2bd3effb76040d6c4b15513fe1079; "
            "different byte size/md5).  The v1.0.0 tag parent history contains the same data and "
            "forecast artifacts; differences between the two commits are the LICENSE/README files."
        ),
        "file_sha256": hashes,
    }

    wide = reconstruct_weekly()
    series_hashes: dict[str, str] = {}
    for location in ALL_COUNTRIES:
        df = pd.DataFrame(
            {"week": wide.index.strftime("%Y-%m-%d"), "cases": wide[location].to_numpy(dtype=float)}
        )
        path = SERIES_DIR / f"weekly_{location.replace(' ', '_')}.csv"
        df.to_csv(path, index=False)
        series_hashes[path.name] = sha256_file(path)
    sums = SERIES_DIR / "SHA256SUMS.txt"
    sums.write_text(
        "".join(f"{h}  {name}\n" for name, h in sorted(series_hashes.items())), encoding="utf-8"
    )
    series_hashes[sums.name] = sha256_file(sums)

    cleaning_checks = verify_cleaning_against_official(wide)

    comparisons_10w = run_comparison(wide, 10, ORIGINS)
    comparisons_11w = run_comparison(wide, 11, SUPP_11W_ORIGINS)

    paper_checks = paper_table_checks()

    discrepancies = [
        {
            "id": "D1",
            "kind": "calibration-window",
            "detail": (
                "Paper text: 'We primarily employed 11-week calibration periods'.  The official "
                "repository ships forecasts for all three calibration lengths (9, 10, 11 weeks) "
                "under Forecasts/*/calibration-{9,10,11} and "
                "n-sub-epidemic/Calibration-{9,10,11}-Weeks; the published headline metrics "
                "reproduce from the Calibration-11 numbers.  Track 1 fidelity was therefore run "
                "on both the 10-week window (task-mandated) and the 11-week window "
                "(paper-primary, supplementary)."
            ),
        },
        {
            "id": "D2",
            "kind": "arima-implementation",
            "detail": (
                "Official ARIMA uses forecast::auto.arima (stepwise search, max.d=2, KPSS-based "
                "d selection, approximation=TRUE); benchlib uses an exhaustive (p,d,q) grid with "
                "p,q in {0,1,2}, d in {0,1} selected by AICc (statsmodels).  Different search "
                "spaces and optimizers can select different orders on the same window."
            ),
        },
        {
            "id": "D3",
            "kind": "subepidemic-implementation",
            "detail": (
                "Official n-sub-epidemic: MATLAB fmincon(sqp) LSQ fits on the cumulative curve "
                "(method-0, dist-0), every wave a three-parameter GLM (flag1=1; the paper states "
                "'a single sub-epidemic followed a three-parameter generalized logistic growth "
                "model'), multi-wave candidates with ESTIMATED onset threshold Cthr, and "
                "data-dependent parameter bounds (r in [mean(|d1:2|)/200, max(|d1:2|)*50], "
                "p in [0,1], K in [20,1e10]).  benchlib's forecast_subepidemic_window instead "
                "ranks {GGM, GLM} single-wave models by AICc with scipy least_squares, fixed-step "
                f"Euler integration (dt={benchlib.EULER_DT}), r capped at 5 and p up to 3.  "
                "Divergences are structural (GGM is not in the official candidate set; the "
                "official top model can be multi-wave) and numerical (bounds/optimizer), not "
                "just optimizer noise."
            ),
        },
        {
            "id": "D4",
            "kind": "prediction-interval",
            "detail": (
                "Official SLR '95% PI' is fit +/- z * se.fit (standard error of the fitted mean "
                "from predict.glm, not a prediction interval); benchlib uses "
                "+/-1.96*sigma_resid*sqrt(h).  Only point forecasts are compared here; interval "
                "forms are documented as a known difference."
            ),
        },
        {
            "id": "D5",
            "kind": "missing-official-artifacts",
            "detail": (
                "The official n-sub-epidemic artifacts omit the first three Brazil origins "
                "(2022-07-07, 07-14, 07-21) at every calibration length; the paper acknowledges "
                "that some forecasts were not produced for Brazil owing to low case counts.  "
                "Track 1 compares Brazil from 2022-07-28 onward so the origin set is identical "
                "across the three models."
            ),
        },
        {
            "id": "D6",
            "kind": "official-artifact-label-swap",
            "detail": (
                "The official MATLAB toolbox forecast files have the two US series SWAPPED: "
                "the 'US-OWID' ranked(1) files embed and were fit to the US(CDC) calibration "
                "series and vice versa (verified date-by-date against the reconstructed "
                "weekly table for BOTH n-sub-epidemic and spatial-wave artifacts; e.g. "
                "US-OWID-01-05-2023.csv calibration rows equal the US(CDC) series exactly).  "
                "Forecast rows in the same files carry the correctly "
                "labelled future observations, so the swap sits in the toolbox input mapping "
                "(column-index based), not in the R cleaning (name-based; ARIMA/SLR verified "
                "exact).  The published n-sub-epidemic/spatial-wave metrics for US(OWID) and "
                "US(CDC) therefore appear to be interchanged.  Track 1 runs the SE comparison "
                "for US(OWID) on the input-aligned (US(CDC)) window."
            ),
        },
    ]

    overall = "pass"
    for block in comparisons_10w.values():
        if block["verdict"] != "pass":
            overall = "fail"

    payload = {
        "track": "track1_fidelity",
        "generated_at": generated,
        "paper": {
            "citation": "Bleichrodt et al. 2024, R. Soc. Open Sci. 11(7):240248",
            "doi": "10.1098/rsos.240248",
        },
        "version_provenance": version_provenance,
        "cleaning_reproduction": {
            "rules": [
                "OWID: date <= 2023-08-09; locations United States/ Brazil/ Spain/ France/ "
                "United Kingdom/ Germany/ Canada/ World; United States relabeled US(OWID); "
                "columns date/new_cases",
                "CDC: Epi_date_V3 (m/d/Y) + Cases, labeled US(CDC)",
                "rbind, weekly aggregation floor_date(week, week_start=4) = Thursday-start "
                "weeks (Thu-Wed), sum of daily new_cases",
                "wide spread by country, drop week 2022-04-28, NA -> 0",
            ],
            "weekly_table_rows": int(len(wide)),
            "weekly_table_weeks": [str(wide.index.min().date()), str(wide.index.max().date())],
            "embedded_window_checks": cleaning_checks,
            "series_sha256": series_hashes,
        },
        "comparison_main_calibration10": {
            model: {k: v for k, v in block.items() if k != "points"}
            for model, block in comparisons_10w.items()
        },
        "comparison_supplementary_calibration11": {
            model: {k: v for k, v in block.items() if k != "points"}
            for model, block in comparisons_11w.items()
        },
        "per_point_detail": {
            "calibration10": {m: b["points"] for m, b in comparisons_10w.items()},
            "calibration11": {m: b["points"] for m, b in comparisons_11w.items()},
        },
        "paper_table_checks": paper_checks,
        "discrepancies": discrepancies,
        "verdict_rule": (
            "ARIMA/SLR: median relative error < 5%; SE-Ranked(1): median relative error < 20% "
            "(optimizer latitude).  Overall = pass iff all three models pass on calibration-10."
        ),
        "overall_verdict": overall,
    }
    json_path = OUT / "track1_fidelity.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"overall_verdict": overall, "json": str(json_path)}, indent=2))
    for label, comp in (("calibration-10", comparisons_10w), ("calibration-11", comparisons_11w)):
        for model, block in comp.items():
            print(
                f"{label} {model}: median_rel={block['median_rel_error']:.4%} "
                f"max_rel={block['max_rel_error']:.4%} verdict={block['verdict']}"
            )
    print("cleaning checks:", json.dumps(cleaning_checks))
    print("paper checks:", json.dumps(paper_checks))


if __name__ == "__main__":
    main()

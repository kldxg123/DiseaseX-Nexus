"""Phase D: immutable DataSnapshot/DataProfile/ForecastTask generation from
trusted live observations (read-only source: epidemic.dashboard_history_series_v2).

Rules enforced:
- one immutable snapshot per live series (country/disease/indicator/frequency);
  snapshot sha256 over ordered (period_start, value, revision) tuples.
- cumulative series are NEVER silently treated as incident counts;
- ultra-short / event series keep their real semantics (no zero-padding into
  fake regular long series);
- profiles are deterministic: same source rows -> same profile_sha256.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date

from .dbutil import sha256_json, sha256_text, stable_uuid

PROFILER_VERSION = "kg_r3c.profiler.v1"

HORIZONS = ["H1", "H2", "H3", "H4"]

CADENCE_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365, "irregular": None}


def classify_semantics(metric_code: str, values: list[float]) -> str:
    mc = metric_code.lower()
    if "cumulative" in mc:
        return "CUMULATIVE"
    if "prevalence" in mc:
        return "PREVALENCE"
    if any(k in mc for k in ("cases_new", "deaths_new", "detections", "reported_cases",
                             "ili_cases", "sari_cases", "sari_deaths", "reports",
                             "specimens", "confirmed_cases", "event")):
        # event series are day-granularity sparse event counts
        if "event" in mc:
            return "EVENT"
        return "INCIDENT"
    # fallback: monotonic non-decreasing => cumulative-like
    if len(values) >= 3 and all(b >= a for a, b in zip(values, values[1:])):
        return "CUMULATIVE_SUSPECTED"
    return "INCIDENT"


def gap_structure(dates: list[date], cadence: str) -> dict:
    step = CADENCE_DAYS.get(cadence)
    if step is None or len(dates) < 2:
        return {"regular": False, "n_gaps": None, "max_gap_days": None,
                "note": "irregular cadence"}
    gaps = []
    for a, b in zip(dates, dates[1:]):
        d = (b - a).days
        if d > step * 1.6:
            gaps.append(d)
    return {"regular": not gaps, "n_gaps": len(gaps),
            "max_gap_days": max(gaps) if gaps else 0}


def trend_evidence(values: list[float]) -> dict:
    n = len(values)
    if n < 8:
        return {"trend": "INSUFFICIENT_DATA", "slope_per_period": None}
    xs = list(range(n))
    mx, my = statistics.fmean(xs), statistics.fmean(values)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return {"trend": "FLAT", "slope_per_period": 0.0}
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / sxx
    # Spearman rho as monotone trend evidence
    rx = _ranks(xs)
    ry = _ranks(values)
    rho = _pearson(rx, ry)
    if abs(rho) < 0.15:
        t = "FLAT"
    elif rho > 0:
        t = "INCREASING" if rho > 0.4 else "WEAK_INCREASING"
    else:
        t = "DECREASING" if rho < -0.4 else "WEAK_DECREASING"
    return {"trend": t, "slope_per_period": slope, "spearman_rho": rho}


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else 0.0


def seasonality_evidence(dates: list[date], values: list[float], cadence: str) -> dict:
    n = len(values)
    if cadence == "week" and n >= 104:
        lag = 52
    elif cadence == "month" and n >= 24:
        lag = 12
    elif cadence == "day" and n >= 14:
        lag = 7
    else:
        return {"seasonality": "NOT_ASSESSABLE", "seasonal_lag": None}
    ac = _pearson(values[:-lag], values[lag:])
    return {"seasonality": "EVIDENCE" if ac > 0.3 else ("WEAK" if ac > 0.15 else "NONE"),
            "seasonal_lag": lag, "lag_autocorr": ac}


def outlier_changepoint(values: list[float]) -> dict:
    n = len(values)
    if n < 8:
        return {"outlier_count": 0, "change_point_suspected": False}
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values]) or 1e-9
    outliers = sum(1 for v in values if abs(v - med) / (1.4826 * mad) > 5)
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) or 1e-9
    cusum, peak, peak_i = 0.0, 0.0, 0
    for i, v in enumerate(values):
        cusum += (v - mean) / sd
        if abs(cusum) > peak:
            peak, peak_i = abs(cusum), i
    return {"outlier_count": outliers,
            "change_point_suspected": peak > 0.35 * n,
            "cusum_peak": peak, "cusum_peak_index": peak_i}


def build_profile_row(series_key: tuple, rows: list[tuple]) -> dict:
    """rows: ordered list of (period_start, period_end, value_numeric, revision, quality_status)."""
    disease, country, metric, cadence = series_key
    dates = [r[0] for r in rows]
    values = [float(r[2]) for r in rows]
    n = len(values)
    n_nonzero = sum(1 for v in values if v != 0)
    mean = statistics.fmean(values)
    var = statistics.pvariance(values) if n > 1 else 0.0
    semantics = classify_semantics(metric, values)
    profile = {
        "country": country, "disease": disease, "indicator": metric,
        "frequency": cadence, "population": None,
        "date_semantics": "period_start..period_end (source-reported)",
        "time_zone": "source-local (aggregated to period)",
        "n_obs": n, "n_nonzero": n_nonzero,
        "zero_ratio": (n - n_nonzero) / n if n else None,
        "missing_ratio": None,  # no explicit missing rows in trusted view; gaps reported separately
        "mean": mean, "variance": var,
        "overdispersion": (var / mean) if mean > 0 else None,
        "min": min(values), "max": max(values),
        "negative_count": sum(1 for v in values if v < 0),
        "first_date": str(dates[0]), "last_date": str(dates[-1]),
        "gap_structure": gap_structure(dates, cadence),
        "trend": trend_evidence(values),
        "seasonality": seasonality_evidence(dates, values, cadence),
        "outliers_changepoints": outlier_changepoint(values),
        "semantics": semantics,
        "is_ultra_short": n < 12,
        "reporting_delay_revision_flags": {
            "has_revisions": any((r[3] or 1) > 1 for r in rows),
            "quality_statuses": sorted({r[4] for r in rows}),
        },
        "available_covariates": [],
        "future_covariates_available": False,
        "forecast_horizons": HORIZONS,
        "data_tier": "TRUSTED_LIVE",
        "leakage_boundary": f"observations with period_end <= forecast_origin; no future covariates used",
    }
    return profile


def series_sha256(rows: list[tuple]) -> str:
    return sha256_text(";".join(f"{r[0]}|{r[2]}|{r[3]}" for r in rows))


def snapshot_and_profile(conn, profiler_version: str = PROFILER_VERSION) -> list[dict]:
    """Create immutable snapshots/profiles/tasks for every live series. Idempotent."""
    sql = """
      SELECT disease_slug, country_alpha3, metric_code, granularity,
             period_start, period_end, value_numeric, revision, quality_status
      FROM epidemic.dashboard_history_series_v2
      WHERE value_numeric IS NOT NULL AND train_eligible
      ORDER BY disease_slug, country_alpha3, metric_code, granularity, period_start
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        all_rows = cur.fetchall()
    series: dict[tuple, list] = {}
    for r in all_rows:
        series.setdefault((r[0], r[1], r[2], r[3]), []).append(r[4:])

    results = []
    with conn.cursor() as cur:
        for key in sorted(series):
            rows = series[key]
            disease, country, metric, cadence = key
            s_sha = series_sha256(rows)
            dates = [r[0] for r in rows]
            snap_id = stable_uuid("snapshot", disease, country, metric, cadence, s_sha)
            cur.execute("""
                INSERT INTO epidemic_kg.kg_data_snapshots
                (snapshot_id, country, disease, indicator, frequency, source_table,
                 n_obs, first_date, last_date, series_sha256, snapshot_sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (snapshot_sha256) DO NOTHING
            """, (snap_id, country, disease, metric, cadence,
                  "epidemic.dashboard_history_series_v2", len(rows),
                  dates[0], dates[-1], s_sha,
                  sha256_text(f"{disease}|{country}|{metric}|{cadence}|{s_sha}")))
            profile = build_profile_row(key, rows)
            p_sha = sha256_json(profile)
            prof_id = stable_uuid("profile", snap_id, p_sha, profiler_version)
            cur.execute("""
                INSERT INTO epidemic_kg.kg_data_profiles
                (profile_id, snapshot_id, profile_json, profile_sha256, profiler_version)
                VALUES (%s,%s,%s::jsonb,%s,%s)
                ON CONFLICT (profile_sha256) DO NOTHING
            """, (prof_id, snap_id, json.dumps(profile, ensure_ascii=False), p_sha, profiler_version))
            cur.execute("SELECT profile_id FROM epidemic_kg.kg_data_profiles WHERE profile_sha256=%s", (p_sha,))
            prof_id = str(cur.fetchone()[0])
            task_id = stable_uuid("task", prof_id, "H1-H4")
            t_sha = sha256_text(f"{prof_id}|H1-H4|{profile['semantics']}")
            cur.execute("""
                INSERT INTO epidemic_kg.kg_forecast_tasks
                (task_id, profile_id, horizons, target_semantics, leakage_boundary, task_sha256)
                VALUES (%s,%s,%s::jsonb,%s,%s,%s)
                ON CONFLICT (task_sha256) DO NOTHING
            """, (task_id, prof_id, json.dumps(HORIZONS), profile["semantics"],
                  profile["leakage_boundary"], t_sha))
            results.append({"series": key, "profile_id": prof_id, "snapshot_id": snap_id,
                            "profile_sha256": p_sha, "profile": profile})
    return results

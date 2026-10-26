"""Profile tests: cumulative semantics, zero inflation, short event series, leakage."""
import datetime as dt

import pytest

from epidemic_core.kg_r3c.profiles import (
    build_profile_row, classify_semantics, gap_structure, series_sha256,
    trend_evidence, seasonality_evidence,
)


def rows(vals, start=dt.date(2024, 1, 1), step_days=7):
    out = []
    d = start
    for v in vals:
        out.append((d, d + dt.timedelta(days=step_days - 1), v, 1, "accepted"))
        d += dt.timedelta(days=step_days)
    return out


class TestSemantics:
    def test_cumulative_by_name(self):
        assert classify_semantics("cases_cumulative", [1, 2, 2, 3]) == "CUMULATIVE"

    def test_incident_cases_new(self):
        assert classify_semantics("cases_new", [5, 3, 8]) == "INCIDENT"

    def test_event_series(self):
        assert classify_semantics("nipah_event_deaths", [1, 0, 1]) == "EVENT"

    def test_monotonic_fallback_suspected(self):
        assert classify_semantics("unknown_metric", [1, 2, 3, 4]) == "CUMULATIVE_SUSPECTED"

    def test_not_cumulative_when_fluctuating(self):
        assert classify_semantics("unknown_metric", [1, 5, 2, 9, 3]) != "CUMULATIVE_SUSPECTED"


class TestProfileStats:
    def test_basic_counts(self):
        p = build_profile_row(("dengue", "BRA", "cases_new", "week"), rows([0, 2, 0, 4, 5, 0, 1, 3]))
        assert p["n_obs"] == 8 and p["n_nonzero"] == 5
        assert abs(p["zero_ratio"] - 3 / 8) < 1e-9
        assert p["negative_count"] == 0
        assert p["min"] == 0 and p["max"] == 5

    def test_overdispersion(self):
        p = build_profile_row(("d", "c", "cases_new", "week"), rows([1, 1, 1, 1, 1, 1, 1, 100]))
        assert p["overdispersion"] > 1.5

    def test_ultra_short_flag(self):
        p = build_profile_row(("nipah", "BGD", "nipah_event_deaths", "day"), rows([1, 0, 1], step_days=1))
        assert p["is_ultra_short"] is True
        assert p["semantics"] == "EVENT"

    def test_negative_counts_detected(self):
        p = build_profile_row(("d", "c", "cases_new", "week"), rows([5, -2, 3, 4, 5, 6, 7, 8]))
        assert p["negative_count"] == 1

    def test_determinism_same_input_same_stats(self):
        r = rows([1, 2, 3, 4, 5, 6, 7, 8])
        p1 = build_profile_row(("d", "c", "m", "week"), r)
        p2 = build_profile_row(("d", "c", "m", "week"), r)
        assert p1 == p2

    def test_series_sha_changes_with_data(self):
        assert series_sha256(rows([1, 2, 3])) != series_sha256(rows([1, 2, 4]))

    def test_leakage_boundary_present(self):
        p = build_profile_row(("d", "c", "cases_new", "week"), rows([1] * 12))
        assert "period_end <= forecast_origin" in p["leakage_boundary"]
        assert p["future_covariates_available"] is False

    def test_horizons_h1_h4(self):
        p = build_profile_row(("d", "c", "cases_new", "week"), rows([1] * 12))
        assert p["forecast_horizons"] == ["H1", "H2", "H3", "H4"]


class TestGaps:
    def test_regular_series(self):
        g = gap_structure([r[0] for r in rows([1] * 10)], "week")
        assert g["regular"] is True and g["n_gaps"] == 0

    def test_gap_detected(self):
        dates = [dt.date(2024, 1, 1) + dt.timedelta(days=7 * i) for i in range(5)]
        dates.append(dates[-1] + dt.timedelta(days=28))
        g = gap_structure(dates, "week")
        assert g["regular"] is False and g["n_gaps"] == 1

    def test_irregular_not_penalized(self):
        g = gap_structure([dt.date(2024, 1, 1), dt.date(2024, 3, 5)], "irregular")
        assert g["note"] == "irregular cadence"


class TestTrendSeasonality:
    def test_increasing_trend(self):
        t = trend_evidence([float(i) for i in range(20)])
        assert t["trend"] == "INCREASING"

    def test_flat(self):
        t = trend_evidence([5.0] * 20)
        assert t["trend"] == "FLAT"

    def test_insufficient(self):
        assert trend_evidence([1.0, 2.0])["trend"] == "INSUFFICIENT_DATA"

    def test_seasonality_weekly(self):
        vals = [10 if i % 52 < 26 else 2 for i in range(104)]
        s = seasonality_evidence([None] * 104, [float(v) for v in vals], "week")
        assert s["seasonal_lag"] == 52 and s["seasonality"] in ("EVIDENCE", "WEAK")

    def test_seasonality_not_assessable_short(self):
        s = seasonality_evidence([None] * 10, [1.0] * 10, "week")
        assert s["seasonality"] == "NOT_ASSESSABLE"

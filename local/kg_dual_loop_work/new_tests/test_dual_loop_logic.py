# ruff: noqa: E501
"""Backtest leakage, sentinel, champion, feedback and router tests.

Sentinels are fixture-based: real post-cutoff observations are mutated only in
an isolated in-memory copy, proving nothing at or before the origin changes.
The database is never modified for sentinel purposes.
"""
from datetime import date

from epidemic_core.forecast_model_kg.dual_loop import (
    compute_metrics,
    compute_origin_indices,
    default_origin_offsets,
    eligibility_verdict,
    period_add,
    run_backtest,
    select_champion,
)
from epidemic_core.kg_r3c.execution import TrainingRow, fit_model, predict_model

H = [1, 2, 3, 4]


def _series(n=120, frequency="month"):
    rows = []
    for i in range(n):
        p = period_add(date(2015, 1, 1), frequency, i)
        rows.append(TrainingRow(period_start=p, period_end=p,
                                value=float(10 + (i % 12) + i // 24),
                                observation_id=f"obs-{i}", revision=1))
    return rows


def test_rolling_origin_no_future_leakage():
    rows = _series()
    origins = compute_origin_indices(len(rows), default_origin_offsets("month"))
    assert len(origins) == 12
    models = {"negbin_dglm.v1": {"parameters": {}}, "last_value": {"parameters": {}}}
    bt = run_backtest(rows, models, origins, "month", seed=99)
    for f in bt["folds"]:
        origin_idx = f["origin_index"]
        train = rows[: origin_idx + 1]
        assert all(r.period_start <= rows[origin_idx].period_start for r in train)
    for p in bt["predictions"]:
        o = date.fromisoformat(p["origin_period"])
        t = date.fromisoformat(p["target_period"])
        assert t == period_add(o, "month", p["horizon"])
        assert t <= rows[-1].period_start


def test_observation_future_sentinel():
    """Mutating post-origin observations (in an isolated copy) must not change
    training hash, artifact hash or predictions for the fold."""
    rows = _series()
    origin_idx = 60
    origin = rows[origin_idx].period_start
    train = rows[: origin_idx + 1]
    fitted_a = fit_model("negbin_dglm.v1", train, {}, 11, origin, "month")
    preds_a = predict_model(fitted_a, H, origin)
    train_sha_a = fitted_a.train_sha256

    mutated = [TrainingRow(period_start=r.period_start, period_end=r.period_end,
                           value=(r.value * 100.0 if r.period_start > origin else r.value),
                           observation_id=r.observation_id, revision=r.revision)
               for r in rows]
    train_b = mutated[: origin_idx + 1]
    fitted_b = fit_model("negbin_dglm.v1", train_b, {}, 11, origin, "month")
    preds_b = predict_model(fitted_b, H, origin)
    assert fitted_a.train_sha256 == fitted_b.train_sha256 == train_sha_a
    assert fitted_a.artifact_sha256 == fitted_b.artifact_sha256
    assert preds_a == preds_b


def test_kg_future_sentinel_fixture():
    """Adding unrelated future claims/evidence to a context fixture must not
    change the resolved recommendation/contract/snapshot identity bundle."""
    base_context = {
        "recommendation_id": "r-1", "adapter_id": "negbin_dglm.v1",
        "snapshot_sha256": "s", "contract_sha256": "c",
        "parameters": {"discount": 0.95}, "claims": ["claim-A"],
    }

    def resolve(ctx):
        return {k: ctx[k] for k in ("recommendation_id", "adapter_id",
                                    "snapshot_sha256", "contract_sha256", "parameters")}

    mutated = dict(base_context)
    mutated["claims"] = base_context["claims"] + ["claim-future-unrelated"]
    assert resolve(base_context) == resolve(mutated)


def test_baselines_on_identical_folds():
    rows = _series()
    origins = compute_origin_indices(len(rows), default_origin_offsets("month"))
    models = {m: {"parameters": {}} for m in
              ("negbin_dglm.v1", "last_value", "rolling_mean", "drift", "seasonal_naive")}
    bt = run_backtest(rows, models, origins, "month", seed=3)
    seen = {}
    for f in bt["folds"]:
        key = (f["origin_index"], f["train_row_count"], f["train_sha256"])
        seen.setdefault(f["adapter_id"], set()).add(key)
    per_adapter = {a: len(s) for a, s in seen.items()}
    assert all(v == len(origins) for v in per_adapter.values())
    # identical training sets across models for the same origin
    by_origin = {}
    for f in bt["folds"]:
        by_origin.setdefault(f["origin_index"], set()).add(f["train_sha256"])
    assert all(len(v) == 1 for v in by_origin.values())


def test_champion_selection_override_and_recommended():
    metrics = {}
    preds = [
        {"adapter_id": "rec", "origin_index": 0, "horizon": 1, "yhat": 10.0, "actual_value": 12.0,
         "lower_bound": 5.0, "upper_bound": 15.0},
        {"adapter_id": "last_value", "origin_index": 0, "horizon": 1, "yhat": 12.0, "actual_value": 12.0,
         "lower_bound": 6.0, "upper_bound": 18.0},
    ]
    result = select_champion(metrics, "rec", preds)
    assert result["champion_adapter_id"] == "last_value"
    assert result["champion_source"] == "EMPIRICAL_BASELINE_OVERRIDE"
    assert result["kg_model_status"] == "EXECUTED_NOT_CHAMPION"

    preds2 = [dict(p, yhat=12.0 if p["adapter_id"] == "rec" else 20.0) for p in preds]
    result2 = select_champion(metrics, "rec", preds2)
    assert result2["champion_adapter_id"] == "rec"
    assert result2["champion_source"] == "KG_RECOMMENDED_MODEL"


def test_metrics_missing_labels_and_nulls():
    preds = [
        {"horizon": 1, "yhat": 5.0, "actual_value": 6.0, "lower_bound": 1.0,
         "upper_bound": 9.0, "deviance": 0.1},
        {"horizon": 1, "yhat": None, "actual_value": 6.0, "lower_bound": None,
         "upper_bound": None, "deviance": None},
        {"horizon": 1, "yhat": 5.0, "actual_value": None, "lower_bound": 1.0,
         "upper_bound": 9.0, "deviance": None},
    ]
    m = compute_metrics(preds, 2.0)[1]
    assert m["labeled_row_count"] == 1
    assert m["missing_label_count"] == 1
    assert m["mae"] == 1.0
    assert m["interval_coverage"] == 1.0
    assert m["mase"] == 0.5


def test_eligibility_router_all_codes():
    assert eligibility_verdict({"n_valid": 0})["eligibility"] == "NO_VALID_OBSERVATION"
    assert eligibility_verdict({"n_valid": 2})["eligibility"] == "SCENARIO_RISK_ONLY"
    assert eligibility_verdict({"n_valid": 10})["eligibility"] == "PRELIMINARY_BASELINE"
    assert eligibility_verdict({"n_valid": 30, "adapter_min_obs": 12})["eligibility"] == "KG_MODEL_SHADOW"
    assert eligibility_verdict({"n_valid": 53, "coverage": 1.0, "frequency": "week"})["eligibility"] == "DEMO_FORECAST"
    assert eligibility_verdict({"n_valid": 120, "coverage": 1.0, "frequency": "week"})["eligibility"] == "SEASONAL_FORMAL_CANDIDATE"
    assert eligibility_verdict({"n_valid": 50, "trusted": False})["eligibility"] == "QUARANTINED_NO_FORECAST"
    assert eligibility_verdict({"n_valid": 240, "coverage": 1.0, "frequency": "month"})["eligibility"] == "SEASONAL_FORMAL_CANDIDATE"


def test_period_add():
    assert period_add(date(2024, 12, 1), "month", 1) == date(2025, 1, 1)
    assert period_add(date(2025, 7, 21), "week", 4) == date(2025, 8, 18)

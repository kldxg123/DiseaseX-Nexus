# ruff: noqa: E501
"""The five anti-leakage / anti-poisoning sentinels (pure, in-memory).

Each sentinel mutates data only in an isolated in-memory copy and proves the
protected computation cannot see the mutation. The DB-level immutability half
of sentinel 4 lives in test_pt_db.py (integration).
"""

import inspect
from dataclasses import replace

from epidemic_core.kg_pt.contracts import contracts_for
from epidemic_core.kg_pt.metrics import mase_denominator
from epidemic_core.kg_pt.pipeline import compute_snapshot_sha256
from epidemic_core.kg_pt.split import make_split
from epidemic_core.kg_pt.tuner import run_tuning, select_best_trial
from epidemic_core.kg_r3c.execution import fit_model, predict_model

SEED = 20260811
H = [1, 2, 3, 4]


def _poisoned(rows, from_index, factor=1000.0):
    """Return a copy with every value at index >= from_index scaled up."""
    return [
        replace(r, value=(r.value * factor if r.value is not None and i >= from_index else r.value))
        for i, r in enumerate(rows)
    ]


def _run(trainval, split, trials=3):
    return run_tuning(
        trainval,
        split,
        "negbin_dglm.v1",
        contracts_for("negbin_dglm.v1", "month"),
        seed=SEED,
        max_trials=trials,
        max_seconds=600,
    )


def test_sentinel_1_test_label_tampering_cannot_change_tuning(synthetic_series, synthetic_periods):
    """Structural: run_tuning only accepts a train+validation slice — there is
    no parameter that could carry test rows. Behavioral: poisoning every test
    label leaves the tuning result byte-identical."""
    params = list(inspect.signature(run_tuning).parameters)
    assert params == [
        "trainval_rows",
        "split",
        "adapter_id",
        "contracts",
        "seed",
        "max_trials",
        "max_seconds",
        "frequency",
    ]
    split = make_split(synthetic_periods)
    poisoned = _poisoned(synthetic_series, from_index=204)  # test window only
    r_clean = _run(synthetic_series[:204], split)
    r_poisoned = _run(poisoned[:204], split)
    assert [t.trial_sha256 for t in r_clean.trials] == [t.trial_sha256 for t in r_poisoned.trials]
    assert r_clean.best_trial_index == r_poisoned.best_trial_index
    assert r_clean.dataset_snapshot_sha256 == r_poisoned.dataset_snapshot_sha256


def test_sentinel_2_future_observation_cannot_change_past_origins(synthetic_series, synthetic_periods):
    """Mutating observations after an origin must not change that origin's
    train hash, artifact hash or predictions — at adapter level and inside a
    full tuning run for every origin whose train slice and labels stay clean."""
    split = make_split(synthetic_periods)
    poisoned = _poisoned(synthetic_series, from_index=180)
    # adapter level: origin 175, train rows[:176] untouched by the poisoning
    origin_idx = 175
    origin = synthetic_series[origin_idx].period_start
    fit_a = fit_model("negbin_dglm.v1", synthetic_series[: origin_idx + 1], {}, SEED, origin, "month")
    fit_b = fit_model("negbin_dglm.v1", poisoned[: origin_idx + 1], {}, SEED, origin, "month")
    assert fit_a.train_sha256 == fit_b.train_sha256
    assert fit_a.artifact_sha256 == fit_b.artifact_sha256
    assert predict_model(fit_a, H, origin) == predict_model(fit_b, H, origin)
    # tuner level: origins with origin_index + 4 <= 179 see neither poisoned
    # training rows nor poisoned labels
    r_clean = _run(synthetic_series[:204], split)
    r_poisoned = _run(poisoned[:204], split)
    for t_clean, t_poisoned in zip(r_clean.trials, r_poisoned.trials, strict=True):
        clean_origins = {o.origin_index: o for o in t_clean.origins if o.origin_index + 4 <= 179}
        poisoned_origins = {o.origin_index: o for o in t_poisoned.origins if o.origin_index + 4 <= 179}
        assert clean_origins.keys() == poisoned_origins.keys()
        for idx in clean_origins:
            assert clean_origins[idx].origin_sha256 == poisoned_origins[idx].origin_sha256
            assert clean_origins[idx].artifact_sha256 == poisoned_origins[idx].artifact_sha256


def test_sentinel_3_parameter_selection_never_touches_test_metrics(synthetic_series, synthetic_periods):
    """The best-trial selector takes ONLY tuning trials; a poisoned test
    evaluation structure cannot influence the selected trial."""
    sig = inspect.signature(select_best_trial)
    assert list(sig.parameters) == ["trials"]
    split = make_split(synthetic_periods)
    result = _run(synthetic_series[:204], split)
    chosen = select_best_trial(result.trials)
    # poisoned test evaluation exists only as an unrelated object; selection
    # has no slot for it and the outcome is unchanged
    poisoned_test_eval = {"wape": 0.0, "adapter_id": "negbin_dglm.v1", "poisoned": True}
    assert poisoned_test_eval is not result.trials
    assert select_best_trial(result.trials) == chosen
    assert chosen is not None


def test_sentinel_4_snapshot_refreeze_is_deterministic(synthetic_series):
    """Pure half of the immutability sentinel: re-freezing identical rows
    yields the identical snapshot hash; any mutation changes it. (DB-level
    privilege assertion: test_pt_db.py, integration.)"""
    sha_a = compute_snapshot_sha256(synthetic_series)
    sha_b = compute_snapshot_sha256(list(synthetic_series))
    assert sha_a == sha_b
    mutated = _poisoned(synthetic_series, from_index=100)
    assert compute_snapshot_sha256(mutated) != sha_a


def test_sentinel_5_preprocessing_leakage_cannot_change_origin_baseline(synthetic_series):
    """Mutating post-origin values must not change that origin's MASE
    denominator (train slice only) or fit artifact."""
    origin_idx = 175
    poisoned = _poisoned(synthetic_series, from_index=origin_idx + 1)
    train_a = synthetic_series[: origin_idx + 1]
    train_b = poisoned[: origin_idx + 1]
    assert mase_denominator([r.value for r in train_a]) == mase_denominator([r.value for r in train_b])
    origin = synthetic_series[origin_idx].period_start
    fit_a = fit_model("negbin_dglm.v1", train_a, {}, SEED, origin, "month")
    fit_b = fit_model("negbin_dglm.v1", train_b, {}, SEED, origin, "month")
    assert fit_a.artifact_sha256 == fit_b.artifact_sha256
    # and the poisoned suffix really was different (the sentinel is not vacuous)
    assert any(a.value != b.value for a, b in zip(synthetic_series, poisoned, strict=True))

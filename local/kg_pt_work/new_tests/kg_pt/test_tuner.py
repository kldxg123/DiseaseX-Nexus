# ruff: noqa: E501
"""Tuner behavior tests: reproducibility, failure recovery, timeout, gates."""

import pytest

from epidemic_core.kg_pt import tuner as tuner_mod
from epidemic_core.kg_pt.contracts import contracts_for, default_params
from epidemic_core.kg_pt.split import make_split
from epidemic_core.kg_pt.tuner import run_tuning, select_best_trial
from epidemic_core.kg_r3c.execution import train_sha

SEED = 20260811
TRIALS = 6


@pytest.fixture(scope="module")
def trainval(synthetic_series):
    return synthetic_series[:204]


@pytest.fixture(scope="module")
def split(synthetic_periods):
    return make_split(synthetic_periods)


@pytest.fixture(scope="module")
def contracts():
    return contracts_for("negbin_dglm.v1", "month")


@pytest.fixture(scope="module")
def result(trainval, split, contracts):
    return run_tuning(trainval, split, "negbin_dglm.v1", contracts, seed=SEED, max_trials=TRIALS, max_seconds=600)


def test_trial_zero_is_contract_defaults(result, contracts):
    assert len(result.trials) == TRIALS
    trial0 = result.trials[0]
    assert trial0.trial_index == 0
    assert trial0.params == default_params(contracts)
    assert trial0.failure_reason is None
    assert trial0.gate_pass is True
    assert trial0.complexity_penalty == 0.0
    assert trial0.objective_value == trial0.common_wape


def test_same_seed_reproduces_byte_identical_trial_hashes(trainval, split, contracts):
    r1 = run_tuning(trainval, split, "negbin_dglm.v1", contracts, seed=SEED, max_trials=TRIALS, max_seconds=600)
    r2 = run_tuning(trainval, split, "negbin_dglm.v1", contracts, seed=SEED, max_trials=TRIALS, max_seconds=600)
    assert [t.trial_sha256 for t in r1.trials] == [t.trial_sha256 for t in r2.trials]
    assert [t.params for t in r1.trials] == [t.params for t in r2.trials]
    assert [t.objective_value for t in r1.trials] == [t.objective_value for t in r2.trials]
    assert r1.best_trial_index == r2.best_trial_index


def test_failed_trial_is_recorded_and_run_recovers(trainval, split, contracts, monkeypatch):
    """One exploding trial must not kill the run; with the same fault injected
    the whole run still reproduces byte-identical trial hashes."""
    original_fit = tuner_mod.fit_model
    calls = {"n": 0}

    def flaky_fit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 13:  # trial 1's first origin fit (trial 0 uses calls 1..12)
            raise RuntimeError("boom-synthetic-fault")
        return original_fit(*args, **kwargs)

    def run():
        calls["n"] = 0
        with monkeypatch.context() as m:
            m.setattr(tuner_mod, "fit_model", flaky_fit)
            return run_tuning(trainval, split, "negbin_dglm.v1", contracts, seed=SEED, max_trials=4, max_seconds=600)

    r1 = run()
    assert len(r1.trials) == 4  # all trials attempted
    failed = r1.trials[1]
    assert failed.gate_pass is False
    assert "boom-synthetic-fault" in failed.failure_reason
    assert r1.best_trial_index is not None and r1.best_trial_index != 1
    assert r1.status == "COMPLETED"
    r2 = run()
    assert [t.trial_sha256 for t in r1.trials] == [t.trial_sha256 for t in r2.trials]


def test_timeout_stops_early_but_keeps_trial_zero(trainval, split, contracts):
    result = run_tuning(trainval, split, "negbin_dglm.v1", contracts, seed=SEED, max_trials=TRIALS, max_seconds=0)
    assert result.stopped_early is True
    assert 1 <= len(result.trials) < TRIALS
    assert result.trials[0].trial_index == 0


def test_gates_present_and_effective(result):
    for t in result.trials:
        assert set(t.gates) == {"non_negative", "finite", "min_folds", "coverage_floor"}
        assert t.gate_pass == (all(t.gates.values()) and t.common_wape is not None)
    # synthetic seasonal data gives plenty of labeled folds and sane coverage
    assert result.trials[0].gates["min_folds"] is True
    assert result.trials[0].gates["coverage_floor"] is True


def test_all_trials_failing_yields_no_best(trainval, split, contracts):
    result = run_tuning(trainval, split, "unknown.adapter", contracts, seed=SEED, max_trials=3, max_seconds=600)
    assert result.status == "FAILED_ALL_TRIALS"
    assert result.best_trial_index is None
    assert all(t.failure_reason and "UNKNOWN_ADAPTER" in t.failure_reason for t in result.trials)


def test_select_best_trial_tie_breaks(result):
    best = select_best_trial(result.trials)
    assert best == result.best_trial_index
    passing = [t for t in result.trials if t.gate_pass]
    assert passing
    best_obj = min(t.objective_value for t in passing)
    assert result.trials[best].objective_value == best_obj


def test_tuner_dataset_hash_covers_trainval_only(synthetic_series, result):
    trainval = synthetic_series[:204]
    assert result.dataset_snapshot_sha256 == train_sha(trainval)
    assert result.dataset_snapshot_sha256 != train_sha(synthetic_series)

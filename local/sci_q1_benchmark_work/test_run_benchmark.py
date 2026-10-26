from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_benchmark as bench


def synthetic_frame(n: int = 180) -> pd.DataFrame:
    index = np.arange(n)
    return pd.DataFrame(
        {
            "year": 2000 + index // 52,
            "week": index % 52 + 1,
            "cases": 40 + 10 * np.sin(2 * np.pi * index / 52) + index * 0.05,
        }
    )


def test_metrics_wape_and_agreement() -> None:
    actual = np.array([10.0, 20.0, 30.0])
    predicted = np.array([9.0, 22.0, 27.0])
    result = bench.metrics(actual, predicted)
    assert result["wape"] == 0.1
    assert result["agreement"] == 0.9


def test_origin_features_do_not_read_future_labels() -> None:
    frame = synthetic_frame()
    y = frame["cases"].to_numpy()
    baseline = bench.origin_features(y, frame, 120, 8)
    mutated = y.copy()
    mutated[120:] += 100000
    changed = bench.origin_features(mutated, frame, 120, 8)
    np.testing.assert_array_equal(baseline, changed)


def test_deterministic_candidate_future_sentinel() -> None:
    frame = synthetic_frame()
    y = frame["cases"].to_numpy()
    spec = bench.ModelSpec("seasonal", "seasonal_scaled", {"years_back": 3, "statistic": "median", "scale_window": 13})
    first = bench.predict_spec(spec, y, frame, 150, 10)
    mutated = y.copy()
    mutated[150:] += 99999
    second = bench.predict_spec(spec, mutated, frame, 150, 10)
    np.testing.assert_array_equal(first, second)


def test_analog_forecast_nonnegative_and_sized() -> None:
    frame = synthetic_frame()
    y = frame["cases"].to_numpy()
    predicted = bench.predict_analog(y, 150, 10, context=13, neighbors=5)
    assert predicted.shape == (10,)
    assert np.all(predicted >= 0)


def test_lock_hash_detects_mutation(tmp_path: Path) -> None:
    lock = {"status": "LOCKED_BEFORE_TEST", "selected_method": "demo"}
    digest = bench.canonical_sha256(lock)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps({**lock, "lock_sha256": digest}), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    expected = loaded.pop("lock_sha256")
    assert bench.canonical_sha256(loaded) == expected
    loaded["selected_method"] = "mutated"
    assert bench.canonical_sha256(loaded) != expected


def test_pooled_matrix_is_origin_safe() -> None:
    frame = synthetic_frame(170)
    y = frame["cases"].to_numpy()
    x1, target1, _ = bench.pooled_training_matrix(y, frame, 150, 13)
    mutated = y.copy()
    mutated[150:] += 100000
    x2, target2, _ = bench.pooled_training_matrix(mutated, frame, 150, 13)
    np.testing.assert_array_equal(x1, x2)
    np.testing.assert_array_equal(target1, target2)

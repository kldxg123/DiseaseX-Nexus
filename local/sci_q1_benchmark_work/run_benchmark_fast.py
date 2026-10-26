from __future__ import annotations

import numpy as np
from sklearn.pipeline import Pipeline

import run_benchmark as bench


_original_make_ml_specs = bench.make_ml_specs
_matrix_cache: dict[
    tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
] = {}


def make_ml_specs_fast() -> list[bench.ModelSpec]:
    return [
        spec
        for spec in _original_make_ml_specs()
        if spec.family != "poisson" and not spec.name.startswith("ridge_a0.1_")
    ]


def fit_predict_ml_cached(
    spec: bench.ModelSpec,
    y: np.ndarray,
    frame,
    origin: int,
    horizon: int,
) -> np.ndarray:
    cache_key = (origin, horizon)
    if cache_key not in _matrix_cache:
        x_train, y_train, ages = bench.pooled_training_matrix(
            y, frame, origin, bench.MAX_HORIZON
        )
        x_future = np.vstack(
            [bench.origin_features(y, frame, origin, h) for h in range(1, horizon + 1)]
        )
        _matrix_cache[cache_key] = (x_train, y_train, ages, x_future)
    x_train, y_train, ages, x_future = _matrix_cache[cache_key]
    transform = str(spec.params.get("transform", "raw"))
    target = np.log1p(y_train) if transform == "log1p" else y_train
    half_life = float(spec.params.get("half_life", 260.0))
    weights = np.power(0.5, ages / half_life)
    estimator = bench.build_estimator(spec)
    if isinstance(estimator, Pipeline):
        estimator.fit(x_train, target, model__sample_weight=weights)
    else:
        estimator.fit(x_train, target, sample_weight=weights)
    predicted = np.asarray(estimator.predict(x_future), dtype=float)
    if transform == "log1p":
        predicted = np.expm1(predicted)
    return np.clip(predicted, 0.0, None)


bench.make_ml_specs = make_ml_specs_fast
bench.fit_predict_ml = fit_predict_ml_cached


if __name__ == "__main__":
    bench.main()

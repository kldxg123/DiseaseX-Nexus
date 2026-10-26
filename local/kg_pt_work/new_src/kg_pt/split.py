"""Strict temporal train/validation/test splitting and rolling-origin specs.

The split is index-based over an ordered period axis with hard boundary
validation: ``train_end < validation_start <= validation_end < test_start``.
Origins never look past their own period: ``feature_max_period`` equals the
origin period and every label period is strictly after it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_pt import HORIZONS, TEST_N, TEST_OFFSETS, TRAIN_N, TUNING_OFFSETS, VAL_N
from epidemic_core.kg_r3c.execution import sha256_json

# An origin needs enough history for seasonal models to be meaningful.
MIN_ORIGIN_INDEX = 59


@dataclass(frozen=True)
class TemporalSplit:
    """Index + date boundaries of one temporal split (test may be empty at the
    end of a demo series; then test_start/test_end are None and test_rows=0)."""

    n_rows: int
    train_n: int
    val_n: int
    test_n: int
    train_start: date | None
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date | None
    test_end: date | None
    train_rows: int
    validation_rows: int
    test_rows: int
    split_sha256: str


@dataclass(frozen=True)
class OriginSpec:
    """One rolling-origin evaluation point with its leakage boundary."""

    origin_index: int
    origin_period: date
    issue_period: date
    data_through: date
    feature_max_period: date
    label_periods: tuple[date, ...]
    train_row_count: int
    validation_row_count: int  # label count inside the evaluation window


def make_split(
    periods: Sequence[date], train_n: int = TRAIN_N, val_n: int = VAL_N, test_n: int = TEST_N
) -> TemporalSplit:
    """Build a validated temporal split over the first train+val+test periods.

    Raises ValueError('SPLIT_INFEASIBLE:...') when there are not enough rows
    or the strict boundary ordering is violated.
    """
    n = len(periods)
    if train_n < 1 or val_n < 1 or test_n < 0:
        raise ValueError(f"SPLIT_INFEASIBLE: negative or empty segment train_n={train_n} val_n={val_n} test_n={test_n}")
    if n < train_n + val_n + test_n:
        raise ValueError(
            f"SPLIT_INFEASIBLE: need {train_n + val_n + test_n} periods "
            f"(train={train_n} val={val_n} test={test_n}) but series has {n}"
        )
    train_start = periods[0]
    train_end = periods[train_n - 1]
    validation_start = periods[train_n]
    validation_end = periods[train_n + val_n - 1]
    if not train_end < validation_start:
        raise ValueError(f"SPLIT_INFEASIBLE: train_end {train_end} must be < validation_start {validation_start}")
    if not validation_start <= validation_end:
        raise ValueError(
            f"SPLIT_INFEASIBLE: validation_start {validation_start} must be <= validation_end {validation_end}"
        )
    test_start: date | None = None
    test_end: date | None = None
    if test_n > 0:
        test_start = periods[train_n + val_n]
        test_end = periods[train_n + val_n + test_n - 1]
        if not validation_end < test_start:
            raise ValueError(f"SPLIT_INFEASIBLE: validation_end {validation_end} must be < test_start {test_start}")
        if not test_start <= test_end:
            raise ValueError(f"SPLIT_INFEASIBLE: test_start {test_start} must be <= test_end {test_end}")
    sha = sha256_json(
        {
            "train_n": train_n,
            "val_n": val_n,
            "test_n": test_n,
            "train_start": train_start.isoformat(),
            "train_end": train_end.isoformat(),
            "validation_start": validation_start.isoformat(),
            "validation_end": validation_end.isoformat(),
            "test_start": test_start.isoformat() if test_start else None,
            "test_end": test_end.isoformat() if test_end else None,
        }
    )
    return TemporalSplit(
        n_rows=n,
        train_n=train_n,
        val_n=val_n,
        test_n=test_n,
        train_start=train_start,
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        test_start=test_start,
        test_end=test_end,
        train_rows=train_n,
        validation_rows=val_n,
        test_rows=test_n,
        split_sha256=sha,
    )


def tuning_origin_indices(n_rows: int, split: TemporalSplit, offsets: list[int] | None = None) -> list[int]:
    """Tuning origins relative to the validation-end index.

    origin_index = (train_n + val_n - 1) - offset, kept only when the origin
    has enough history (>= MIN_ORIGIN_INDEX) and its H1-H4 labels stay inside
    the train+validation window.
    """
    offsets = TUNING_OFFSETS if offsets is None else offsets
    val_end_idx = split.train_n + split.val_n - 1
    origins: list[int] = []
    for off in offsets:
        idx = val_end_idx - off
        if idx >= MIN_ORIGIN_INDEX and idx + max(HORIZONS) <= val_end_idx and idx + max(HORIZONS) <= n_rows - 1:
            origins.append(idx)
    return origins


def test_origin_indices(n_rows: int, split: TemporalSplit, offsets: list[int] | None = None) -> list[int]:
    """Independent-test origins relative to the series-end index."""
    offsets = TEST_OFFSETS if offsets is None else offsets
    last = n_rows - 1
    origins: list[int] = []
    for off in offsets:
        idx = last - off
        if idx >= MIN_ORIGIN_INDEX and idx + max(HORIZONS) <= last:
            origins.append(idx)
    return origins


def build_origin_spec(
    periods: Sequence[date],
    origin_index: int,
    frequency: str,
    label_window: tuple[date, date] | None = None,
) -> OriginSpec:
    """Assemble the leakage-boundary record for one origin.

    Hard invariants: feature_max_period <= origin_period and every label
    period is strictly after the origin period.
    """
    origin_period = periods[origin_index]
    feature_max_period = periods[origin_index]  # training rows are periods[:origin_index+1]
    label_periods = tuple(period_add(origin_period, frequency, h) for h in HORIZONS)
    if not feature_max_period <= origin_period:
        raise ValueError(f"ORIGIN_LEAKAGE: feature_max_period {feature_max_period} after origin {origin_period}")
    if not all(label > origin_period for label in label_periods):
        raise ValueError(f"ORIGIN_LEAKAGE: label period not after origin {origin_period}")
    in_window = 0
    if label_window is not None:
        start, end = label_window
        in_window = sum(1 for label in label_periods if start <= label <= end)
    return OriginSpec(
        origin_index=origin_index,
        origin_period=origin_period,
        issue_period=origin_period,
        data_through=origin_period,
        feature_max_period=feature_max_period,
        label_periods=label_periods,
        train_row_count=origin_index + 1,
        validation_row_count=in_window,
    )

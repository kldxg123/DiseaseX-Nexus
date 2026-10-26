from __future__ import annotations

from datetime import date, timedelta

from common.db import get_session, init_db
from common.models import ModelFeature
from predictor_agent.datasets import (
    FeatureRecord,
    FeatureRow,
    group_rows_by_series,
    load_feature_records,
    load_feature_rows,
    split_train_test_by_date,
)


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'predictor-datasets.db'}"


def _seed_features(database_url: str) -> None:
    start = date(2025, 1, 6)
    with get_session(database_url) as session:
        for horizon_days in (7, 28):
            for index in (2, 0, 1):
                session.add(
                    ModelFeature(
                        country="United States",
                        admin1=None,
                        disease="norovirus",
                        target_date=start + timedelta(days=7 * index),
                        iso_week=f"2025-W{index + 1:02d}",
                        horizon_days=horizon_days,
                        feature_json={"current_week_cases": index + 1},
                        target_value=float(index + horizon_days),
                        target_outbreak_label=index % 2 == 0,
                        split_type="train",
                    )
                )
        session.add(
            ModelFeature(
                country="France",
                admin1=None,
                disease="norovirus",
                target_date=start,
                iso_week="2025-W01",
                horizon_days=7,
                feature_json={"current_week_cases": 1},
                target_value=1.0,
                target_outbreak_label=False,
                split_type="test",
            )
        )


def test_load_feature_rows_filters_and_orders_rows(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        rows = load_feature_rows(
            session,
            country="United States",
            disease="norovirus",
            split_type="train",
            horizons_days=(7,),
        )

    assert [row.iso_week for row in rows] == ["2025-W01", "2025-W02", "2025-W03"]
    assert {row.horizon_days for row in rows} == {7}
    assert all(isinstance(row, FeatureRow) for row in rows)


def test_group_rows_by_series_groups_and_sorts() -> None:
    rows = [
        FeatureRow(
            id=2,
            country="United States",
            admin1=None,
            disease="norovirus",
            target_date=date(2025, 1, 13),
            iso_week="2025-W02",
            horizon_days=7,
            feature_json={},
            target_value=2.0,
            target_outbreak_label=False,
            split_type="train",
        ),
        FeatureRow(
            id=1,
            country="United States",
            admin1=None,
            disease="norovirus",
            target_date=date(2025, 1, 6),
            iso_week="2025-W01",
            horizon_days=7,
            feature_json={},
            target_value=1.0,
            target_outbreak_label=False,
            split_type="train",
        ),
    ]

    groups = group_rows_by_series(rows)

    assert list(groups) == [("United States", None, "norovirus", 7)]
    assert [row.iso_week for row in groups[("United States", None, "norovirus", 7)]] == [
        "2025-W01",
        "2025-W02",
    ]


def test_load_feature_records_filters_sorts_and_splits(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        records = load_feature_records(
            session,
            country="United States",
            disease="norovirus",
            horizon_days=7,
            split_type="train",
        )

    assert all(isinstance(record, FeatureRecord) for record in records)
    assert [record.iso_week for record in records] == ["2025-W01", "2025-W02", "2025-W03"]
    assert records[0].target_value == 7.0

    train, test = split_train_test_by_date(records, cutoff_date=date(2025, 1, 13))

    assert [record.iso_week for record in train] == ["2025-W01"]
    assert [record.iso_week for record in test] == ["2025-W02", "2025-W03"]

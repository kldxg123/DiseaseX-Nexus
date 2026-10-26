from __future__ import annotations

from datetime import date

from common.db import get_session, init_db
from common.models import ModelFeature
from cleaner_agent.feature_store import ModelFeatureStore


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'model-feature-store.db'}"


def _feature_row(value: float = 3.0) -> dict:
    return {
        "country": "United States",
        "admin1": None,
        "disease": "norovirus",
        "target_date": date(2025, 1, 13),
        "iso_week": "2025-W01",
        "horizon_days": 7,
        "feature_json": {"current_week_cases": 1, "holiday_flag": False},
        "target_value": value,
        "target_outbreak_label": False,
        "split_type": "train",
    }


def test_model_feature_store_saves_updates_and_skips_duplicates(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        store = ModelFeatureStore(session)
        first = store.save_features([_feature_row()])
        second = store.save_features([_feature_row(5.0)], replace_existing=True)
        third = store.save_features([_feature_row(7.0)], replace_existing=False)
        count = store.count_features(country="United States", disease="norovirus")
        saved = session.query(ModelFeature).one()

    assert first == {"rows_seen": 1, "saved": 1, "updated": 0, "skipped": 0}
    assert second == {"rows_seen": 1, "saved": 0, "updated": 1, "skipped": 0}
    assert third == {"rows_seen": 1, "saved": 0, "updated": 0, "skipped": 1}
    assert count == 1
    assert saved.target_value == 5.0

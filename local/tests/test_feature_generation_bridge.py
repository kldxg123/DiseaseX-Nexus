from __future__ import annotations

from common.db import get_session
from common.models import ModelFeature
from live_source_search_orchestrator.feature_generation_bridge import try_generate_features_from_acquisition

from acquisition_quality_helpers import make_acquisition_db, make_run_dir


def test_feature_generation_bridge_skips_when_gate_blocks(tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    run_dir = make_run_dir(tmp_path)

    result = try_generate_features_from_acquisition(database_url, "measles", "United States", run_dir)

    assert result["status"] == "skipped"
    assert result["feature_generation_attempted"] is False
    assert "insufficient_temporal_signal" in result["blocker_types"]
    with get_session(database_url) as session:
        assert session.query(ModelFeature).count() == 0


def test_feature_generation_bridge_runs_only_for_feature_ready_series(tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=5)
    run_dir = make_run_dir(tmp_path)

    result = try_generate_features_from_acquisition(database_url, "measles", "United States", run_dir, horizons_days=(7,))

    assert result["status"] == "ok"
    assert result["feature_generation_attempted"] is True
    assert result["feature_rows"] > 0
    with get_session(database_url) as session:
        assert session.query(ModelFeature).count() > 0

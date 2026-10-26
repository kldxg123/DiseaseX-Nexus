from __future__ import annotations

import json

from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, Prediction, RawDocument, SourceRegistry
from worker_agent import PipelineRunner, TaskRunner


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'worker-pipeline.db').as_posix()}"


def test_full_local_pipeline_runs_with_synthetic_data(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        summary = PipelineRunner(TaskRunner(session)).run_full_local_pipeline(
            country="United States",
            disease="norovirus",
            horizon_days=28,
        )

        assert summary["status"] in {"success", "partial"}
        assert summary["sources"] > 0
        assert summary["raw_documents"] >= 20
        assert summary["events"] >= 12
        assert summary["features"] > 0
        assert summary["predictions"] >= 0
        assert "minimum_data" in summary
        assert session.query(SourceRegistry).count() == summary["sources"]
        assert session.query(RawDocument).count() == summary["raw_documents"]
        assert session.query(EpidemicEvent).count() == summary["events"]
        assert session.query(ModelFeature).count() == summary["features"]
        assert session.query(Prediction).count() == summary["predictions"]

    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "feature_json" not in rendered
    assert "lesson_json" not in rendered
    assert "<html>" not in rendered
    assert "password" not in rendered
    assert "token" not in rendered

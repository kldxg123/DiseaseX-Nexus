from pathlib import Path


def test_dashboard_migration_surface() -> None:
    text = Path("migrations/versions/0004_p1_6_latest_window_dashboard.py").read_text()
    for name in ("dashboard_snapshot_catalog_v1", "dashboard_observation_series_v1", "dashboard_quality_summary_v1"):
        assert f"CREATE VIEW epidemic.{name}" in text
    assert 'op.execute(f"REVOKE ALL ON epidemic.{view} FROM PUBLIC")' in text
    for forbidden in ("storage_uri", "claim_token", "lease_expires_at", "password", "pg_read_file"):
        assert forbidden not in text
    assert "metric_definitions SET" not in text
    assert "retrieval_window_resolutions" in text

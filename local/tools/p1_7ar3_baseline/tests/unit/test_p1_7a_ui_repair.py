from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_migration_is_additive_and_least_privilege() -> None:
    migration = (ROOT / "migrations/versions/0006_p1_7a_ui_read_models.py").read_text(encoding="utf-8")
    assert 'down_revision = "0005_p1_7_source_discovery_review"' in migration
    for view in (
        "dashboard_history_series_v2",
        "dashboard_catalog_options_v2",
        "review_mapping_integrity_v2",
        "review_field_mapping_v2",
        "review_candidate_cards_v2",
    ):
        assert view in migration
    assert "SECURITY DEFINER" in migration
    assert "REVOKE ALL ON FUNCTION" in migration
    assert "GRANT EXECUTE ON FUNCTION" in migration
    assert "MAPPING_SOURCE_FIELD_MISSING" in migration
    assert "0005_p1_7_source_discovery_review.py" not in migration


def test_review_console_is_html_first_and_uses_local_assets() -> None:
    app = (ROOT / "src/epidemic_core/discovery/review_app.py").read_text(encoding="utf-8")
    script = (ROOT / "src/epidemic_core/discovery/static/review.p1-7ar3-v1.js").read_text(encoding="utf-8")
    css = (ROOT / "src/epidemic_core/discovery/static/review.p1-7ar3-v1.css").read_text(encoding="utf-8")
    assert 'RedirectResponse("/reviews", 303)' in app
    assert "<pre>" not in app and "<details>" in app
    assert '<table id="mapping-table">' in app
    assert "批准当前版本并排队入库" in app and "disabled" in app
    assert "原始数据与清洗结果预览" in app
    assert "来源可信度、数据质量与入库风险" in app
    assert "innerHTML" not in script
    assert "localStorage" not in script and "sessionStorage" not in script
    assert "@media(max-width:600px)" in css


def test_queue_observability_and_semantic_labels_are_versioned() -> None:
    migration = (ROOT / "migrations/versions/0007_p1_7a_discovery_observability.py").read_text(encoding="utf-8")
    labels = (ROOT / "src/epidemic_core/discovery/review_labels.py").read_text(encoding="utf-8")
    for value in (
        "FOR UPDATE SKIP LOCKED",
        "claim_token",
        "lease_expires_at",
        "heartbeat_at",
    ):
        assert value in migration
    for value in (
        "review-ui-labels-v1",
        "mapped",
        "raw-only",
        "UNSUPPORTED_GEOGRAPHIC_GRAIN",
        "MAPPING_SOURCE_FIELD_MISSING",
        "PERSON_LEVEL_DATA",
    ):
        assert value in labels
    worker = (ROOT / "src/epidemic_core/discovery/worker.py").read_text(encoding="utf-8")
    assert "GENERIC_SEARCH_PROVIDER_UNCONFIGURED" in worker


def test_installers_copy_versioned_static_assets() -> None:
    for name in ("dashboard", "source_review"):
        installer = (ROOT / f"scripts/{name}/install_runtime.sh").read_text(encoding="utf-8")
        assert "/static" in installer
        assert "find" in installer and "install" in installer

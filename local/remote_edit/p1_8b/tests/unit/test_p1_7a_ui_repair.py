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
        "review-ui-labels-v3",
        "mapped",
        "raw-only",
        "UNSUPPORTED_GEOGRAPHIC_GRAIN",
        "MAPPING_SOURCE_FIELD_MISSING",
        "PERSON_LEVEL_DATA",
    ):
        assert value in labels
    worker = (ROOT / "src/epidemic_core/discovery/worker.py").read_text(encoding="utf-8")
    for value in (
        "DISCOVERY_COMPLETE_NO_CANDIDATE",
        "DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE",
        "PROVIDER_DEGRADED",
        "network_requests",
        "directories_attempted",
    ):
        assert value in worker


def test_r4_result_page_distinguishes_coverage_and_terminal_lease() -> None:
    app = (ROOT / "src/epidemic_core/discovery/review_app.py").read_text(encoding="utf-8")
    for value in (
        "实际搜索覆盖",
        "查询摘要",
        "0 个候选不代表不存在数据源",
        "任务已结束，租约已释放",
        "创建关联重试任务",
        "系统缺少搜索 Provider 配置",
        "已跳过：无输入",
        "不适用：无候选",
    ):
        assert value in app
    assert "JSON.stringify" not in app


def test_r4_migration_is_additive_and_retry_is_linked() -> None:
    migration = (ROOT / "migrations/versions/0008_p1_7a_discovery_capability.py").read_text(encoding="utf-8")
    assert 'down_revision = "0007_p1_7a_discovery_observability"' in migration
    assert "retry_discovery_run" in migration
    assert "retry_of_run_id" in migration
    assert "INTERNATIONAL_PUBLIC_HEALTH" in migration
    assert "GRANT EXECUTE" in migration and "REVOKE ALL" in migration


def test_installers_copy_versioned_static_assets() -> None:
    for name in ("dashboard", "source_review"):
        installer = (ROOT / f"scripts/{name}/install_runtime.sh").read_text(encoding="utf-8")
        assert "/static" in installer
        assert "find" in installer and "install" in installer


def test_verified_discovery_review_is_table_first_and_keeps_approval_disabled() -> None:
    app = (ROOT / "src/epidemic_core/discovery/review_app.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/0011_p1_8b_verified_discovery.py").read_text(encoding="utf-8")
    for marker in (
        "真实隔离预览",
        'id="verified-preview-table"',
        "Schema 建议映射",
        "请求范围与候选实际范围",
        "批准按钮保持禁用",
        "下载隔离区 CSV",
    ):
        assert marker in app
    assert "source_discovery_search_results" in migration
    assert "candidate_fetches" in migration
    assert "GRANT SELECT" in migration and "REVOKE DELETE" in migration


def test_verified_candidate_card_uses_real_review_ready_state() -> None:
    app = (ROOT / "src/epidemic_core/discovery/review_app.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/0014_p1_8b_review_ready_cards.py").read_text(encoding="utf-8")
    read_views = (ROOT / "migrations/versions/0015_p1_8b_reviewer_read_views.py").read_text(encoding="utf-8")
    assert 'down_revision = "0013_p1_8b_discovery_submit_receipt"' in migration
    assert "v.state='REVIEW_READY'" in migration
    assert "v.candidate_status='REVIEW_READY'" in migration
    assert "true approval_blocked" in migration
    assert 'down_revision = "0014_p1_8b_review_ready_cards"' in read_views
    assert "progress_json,completed_at" in read_views
    assert '<option value="REVIEW_READY">' in app
    assert "FROM epidemic_discovery.review_preview_v" in app
    assert "FROM epidemic_discovery.review_discovery_runs_v2" in app

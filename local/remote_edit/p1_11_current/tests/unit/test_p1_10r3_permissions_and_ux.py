from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (ROOT / "migrations/versions/0020_p1_10r3_preview_permissions.py").read_text(encoding="utf-8")
REVIEW = (ROOT / "src/epidemic_core/p1_10/review_app.py").read_text(encoding="utf-8")
DIAGNOSTICS = (ROOT / "src/epidemic_core/p1_10/diagnostics.py").read_text(encoding="utf-8")
WORKER = (ROOT / "src/epidemic_core/p1_10/preview.py").read_text(encoding="utf-8")
CSS = (ROOT / "src/epidemic_core/discovery/static/review.p1-10-preview.css").read_text(encoding="utf-8")
START = (ROOT / "scripts/preview_publication_worker/start_preview_publication_worker.sh").read_text(encoding="utf-8")
STATUS = (ROOT / "scripts/preview_publication_worker/status_preview_publication_worker.sh").read_text(encoding="utf-8")


def test_revision_is_single_successor_of_p1_10r() -> None:
    assert 'revision = "0020_p1_10r3_preview_permissions"' in MIGRATION
    assert 'down_revision = "0019_p1_10r_formal_preview_http500"' in MIGRATION


def test_migration_never_uses_grant_all() -> None:
    assert "GRANT ALL" not in MIGRATION.upper()


def test_public_cannot_create_in_preview_schema() -> None:
    assert "REVOKE CREATE ON SCHEMA epidemic_preview FROM PUBLIC" in MIGRATION


def test_public_has_no_preview_table_privileges() -> None:
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA epidemic_preview FROM PUBLIC" in MIGRATION


def test_safe_views_have_explicit_owner() -> None:
    assert "ALTER VIEW epidemic_preview.{view} OWNER TO epidemicdb" in MIGRATION


def test_review_reads_safe_fetch_view() -> None:
    assert "preview_candidate_fetches_safe_v1" in MIGRATION
    assert "FROM epidemic_preview.preview_candidate_fetches_safe_v1" in REVIEW


def test_review_cannot_read_candidate_fetch_base_table() -> None:
    assert "REVOKE ALL ON epidemic_discovery.candidate_fetches FROM epidemicreview" in MIGRATION
    assert "FROM epidemic_discovery.candidate_fetches WHERE" not in REVIEW


def test_review_uses_security_definer_functions_for_writes() -> None:
    assert "GRANT EXECUTE ON FUNCTION epidemic_preview.submit_preview_decision" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION epidemic_preview.queue_preview_publication" in MIGRATION


def test_worker_can_update_only_job_state() -> None:
    assert "GRANT UPDATE ON epidemic_preview.preview_publication_jobs TO epidemicpreview" in MIGRATION
    assert "FOR UPDATE OF j SKIP LOCKED" in WORKER


def test_worker_cannot_update_immutable_preview_objects() -> None:
    assert "REVOKE UPDATE ON epidemic_preview.preview_review_decisions" in MIGRATION
    assert "preview_dashboard_publications FROM epidemicpreview" in MIGRATION


def test_worker_receives_exact_event_sequence_privileges() -> None:
    assert "GRANT USAGE,SELECT ON SEQUENCE epidemic_preview.preview_publication_events_id_seq" in MIGRATION


def test_default_privileges_target_actual_preview_owner() -> None:
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_preview" in MIGRATION


def test_default_function_execute_is_revoked_from_public() -> None:
    assert "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC" in MIGRATION


def test_downgrade_restores_previous_worker_update_contract() -> None:
    assert MIGRATION.count("GRANT UPDATE ON epidemic_preview.preview_review_decisions") == 1


def test_dataset_summary_groups_by_logical_endpoint() -> None:
    assert "logical_dataset_key" in MIGRATION
    assert "preview_candidate_dataset_summary_v1" in MIGRATION


def test_logical_versions_use_real_retrieval_order() -> None:
    assert "ORDER BY retrieved_at,version_created_at,candidate_version_id" in MIGRATION


def test_current_version_uses_descending_retrieval_order() -> None:
    assert "ORDER BY retrieved_at DESC,version_created_at DESC,candidate_version_id DESC" in MIGRATION


def test_content_change_is_explicit() -> None:
    assert "content_changed" in MIGRATION
    assert "内容已变化" in MIGRATION


def test_row_count_change_is_explicit() -> None:
    assert "row_count_delta" in MIGRATION
    assert "行数变化" in MIGRATION


def test_schema_change_is_explicit() -> None:
    assert "schema_changed" in MIGRATION
    assert "Schema 已变化" in MIGRATION


def test_date_range_change_is_explicit() -> None:
    assert "date_range_changed" in MIGRATION
    assert "日期范围已变化" in MIGRATION


def test_safe_field_change_is_explicit() -> None:
    assert "safe_fields_changed" in MIGRATION
    assert "安全字段已变化" in MIGRATION


def test_mapping_validity_is_explicit() -> None:
    assert "mapping_still_valid" in MIGRATION
    assert "Mapping 仍有效" in MIGRATION


def test_candidate_list_exposes_grouped_version_history() -> None:
    assert "展开版本历史" in REVIEW
    assert "查看当前版本" in REVIEW


def test_candidate_detail_has_thirteen_chinese_sections() -> None:
    for index in range(1, 14):
        assert f">{index}. " in REVIEW


def test_trusted_approval_remains_disabled() -> None:
    assert "正式可信审批保持禁用" in REVIEW
    assert "正式批准（不可用）" in REVIEW


def test_preview_authorization_binds_version_and_artifact() -> None:
    assert '"candidate_version_id"' in REVIEW
    assert '"artifact_sha256"' in REVIEW


def test_review_readiness_checks_safe_views_and_functions() -> None:
    assert "required_privileges" in DIAGNOSTICS
    assert "has_function_privilege" in DIAGNOSTICS


def test_worker_readiness_checks_required_and_forbidden_writes() -> None:
    assert "preview_permission_readiness" in WORKER
    assert '"daily_update_" + "schedules"' in WORKER


def test_worker_start_and_status_use_readiness_gate() -> None:
    assert "alive && ready" in START
    assert "ready" in STATUS


def test_artifact_and_status_cells_do_not_break_per_character() -> None:
    assert ".nowrap" in CSS
    assert "white-space: nowrap" in CSS
    assert "word-break: normal" in CSS

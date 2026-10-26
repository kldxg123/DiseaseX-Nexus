"""Repair P1-12 preview isolation and publication contracts.

Revision ID: 0024_p1_12_root_cause_repair
Revises: 0023_p1_12_discovery_transition_guard
"""

from alembic import op

revision = "0024_p1_12_root_cause_repair"
down_revision = "0023_p1_12_discovery_transition_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE OR REPLACE VIEW epidemic_preview.active_preview_publications_v1 AS
        SELECT p.*,s.observation_count,s.total_value,s.date_min,s.date_max,s.state_count,s.municipality_count,
          s.snapshot_sha256,d.artifact_sha256,d.schema_sha256,d.mapping_sha256,d.license_evidence_sha256,
          d.discovery_run_id,d.candidate_id,d.candidate_version_id,d.review_case_id,d.reviewer_id,d.reason,
          mc.contract_sha256,mc.metric_code,mc.display_name_zh,mc.unit,mc.data_nature,
          mc.source_date_semantics,mc.geography_semantics,p.id publication_id
        FROM epidemic_preview.preview_dashboard_publications p
        JOIN epidemic_preview.preview_snapshots s ON s.id=p.snapshot_id
        JOIN epidemic_preview.preview_review_decisions d ON d.id=p.preview_decision_id
        JOIN epidemic_preview.preview_mapping_contracts mc ON mc.publication_job_id=p.publication_job_id
        WHERE (SELECT e.to_state FROM epidemic_preview.preview_publication_events e
          WHERE e.publication_job_id=p.publication_job_id ORDER BY e.id DESC LIMIT 1)='PREVIEW_PUBLISHED'"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW epidemic_preview.preview_review_cards_v1 AS
        SELECT rc.id review_case_id,rc.state review_state,rc.state_version,rc.etag,
          cv.id candidate_version_id,cv.candidate_id,cv.license_status,cv.terms_sha256,
          COALESCE((
            SELECT l.discovery_run_id FROM epidemic_discovery.discovery_run_candidate_links l
            WHERE l.candidate_version_id=cv.id ORDER BY l.linked_at DESC,l.id DESC LIMIT 1
          ),sc.discovery_run_id) discovery_run_id,
          sc.publisher,sc.dataset_name,cv.canonical_url_redacted,
          sp.schema_sha256,sp.row_count,sp.column_count,mp.mapping_sha256,
          ar.sha256 artifact_sha256,ar.byte_size,ar.mime_type,qa.pii_status,
          qa.license_status assessed_license_status,d.id preview_decision_id,d.created_at preview_decided_at,
          j.id preview_job_id,j.state preview_state,j.attempt_count,j.failure_code,j.snapshot_id,j.publication_id
        FROM epidemic_discovery.source_review_cases rc
        JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
        JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
        JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
        JOIN epidemic_discovery.source_mapping_proposals mp
          ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
        JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
        JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
        LEFT JOIN epidemic_preview.preview_review_decisions d ON d.review_case_id=rc.id AND d.environment='production'
        LEFT JOIN epidemic_preview.preview_publication_jobs j ON j.preview_decision_id=d.id
        WHERE EXISTS (
          SELECT 1 FROM epidemic_discovery.source_schema_columns col
          WHERE col.schema_profile_id=sp.id
            AND col.source_name IN ('dt_notific','sg_uf_not','id_municip','nu_ano','case_notification_count')
        )
        AND NOT EXISTS (
          SELECT 1 FROM epidemic_p1_12.review_application_bindings rab
          WHERE rab.review_case_id=rc.id
        )"""
    )
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicdb') THEN
          ALTER VIEW epidemic_preview.active_preview_publications_v1 OWNER TO epidemicdb;
          ALTER VIEW epidemic_preview.preview_review_cards_v1 OWNER TO epidemicdb;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT SELECT ON epidemic_preview.preview_review_cards_v1 TO epidemicreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
          GRANT SELECT ON epidemic_preview.active_preview_publications_v1 TO epidemic_dashboard_ro;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_review_cards_v1")
    op.execute("DROP VIEW IF EXISTS epidemic_preview.active_preview_publications_v1 CASCADE")

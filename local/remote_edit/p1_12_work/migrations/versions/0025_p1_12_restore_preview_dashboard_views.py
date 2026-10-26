"""Restore preview dashboard views after P1-12 isolation repair.

Revision ID: 0025_p1_12_restore_preview_dashboard_views
Revises: 0024_p1_12_root_cause_repair
"""

from alembic import op

revision = "0025_p1_12_restore_preview_dashboard_views"
down_revision = "0024_p1_12_root_cause_repair"
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
        """CREATE OR REPLACE VIEW epidemic_preview.preview_dashboard_series_v1 AS
        SELECT p.id publication_id,p.source_key,p.source_name,p.publisher,p.official_domain,p.country_iso2,
          p.disease_slug,p.license_status,p.coverage_status,p.data_tier,p.production_usable,
          p.training_eligible,p.national_aggregate,s.id snapshot_id,s.snapshot_sha256,
          o.id observation_id,o.observation_date,o.state_code,o.municipality_code,o.metric_code,
          o.unit,o.value_numeric,o.quality_status,o.source_row_set_sha256,o.source_row_count,
          o.artifact_sha256,o.mapping_contract_id,o.candidate_version_id,o.lineage_sha256
        FROM epidemic_preview.active_preview_publications_v1 p
        JOIN epidemic_preview.preview_snapshots s ON s.id=p.snapshot_id
        JOIN epidemic_preview.preview_snapshot_observations m ON m.snapshot_id=s.id
        JOIN epidemic_preview.preview_observations o ON o.id=m.observation_id"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW epidemic_preview.preview_lineage_v1 AS
        SELECT p.id publication_id,p.publication_job_id,p.preview_decision_id,p.snapshot_id,
          p.publication_sha256,p.source_key,p.publisher,p.official_domain,p.license_status,p.coverage_status,
          d.discovery_run_id,d.candidate_id,d.candidate_version_id,d.review_case_id,d.artifact_sha256,
          d.schema_sha256,d.mapping_sha256 source_mapping_sha256,d.license_evidence_sha256,d.binding_sha256,
          d.reviewer_id,d.created_at preview_decided_at,mc.id mapping_contract_id,mc.contract_sha256,
          s.snapshot_sha256,s.observation_count,s.total_value,s.created_at snapshot_created_at,
          q.assessment_sha256,q.pii_status,q.status quality_status,j.attempt_count,j.created_at job_created_at,
          p.published_at
        FROM epidemic_preview.preview_dashboard_publications p
        JOIN epidemic_preview.preview_publication_jobs j ON j.id=p.publication_job_id
        JOIN epidemic_preview.preview_review_decisions d ON d.id=p.preview_decision_id
        JOIN epidemic_preview.preview_mapping_contracts mc ON mc.publication_job_id=p.publication_job_id
        JOIN epidemic_preview.preview_snapshots s ON s.id=p.snapshot_id
        JOIN epidemic_preview.preview_quality_assessments q ON q.publication_job_id=p.publication_job_id"""
    )
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicdb') THEN
          ALTER VIEW epidemic_preview.active_preview_publications_v1 OWNER TO epidemicdb;
          ALTER VIEW epidemic_preview.preview_dashboard_series_v1 OWNER TO epidemicdb;
          ALTER VIEW epidemic_preview.preview_lineage_v1 OWNER TO epidemicdb;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
          GRANT SELECT ON epidemic_preview.active_preview_publications_v1,
            epidemic_preview.preview_dashboard_series_v1,
            epidemic_preview.preview_lineage_v1 TO epidemic_dashboard_ro;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_lineage_v1")
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_dashboard_series_v1")

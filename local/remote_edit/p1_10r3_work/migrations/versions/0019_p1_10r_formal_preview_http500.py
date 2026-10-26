from __future__ import annotations

from alembic import op

revision = "0019_p1_10r_formal_preview_http500"
down_revision = "0018_p1_10_preview_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE VIEW epidemic_preview.preview_safe_source_columns_v1 AS
        SELECT rc.id review_case_id,c.ordinal,c.source_name
        FROM epidemic_discovery.source_review_cases rc
        JOIN epidemic_discovery.source_schema_profiles sp
          ON sp.candidate_version_id=rc.candidate_version_id
        JOIN epidemic_discovery.source_schema_columns c ON c.schema_profile_id=sp.id"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.preview_discovery_candidate_links_v1 AS
        SELECT l.id link_id,l.discovery_run_id,l.candidate_id,l.candidate_version_id,
          l.search_result_id,l.fetch_id,l.artifact_sha256,l.link_reason,l.linked_at,
          rc.id review_case_id,sc.publisher,sc.dataset_name
        FROM epidemic_discovery.discovery_run_candidate_links l
        JOIN epidemic_discovery.source_candidates sc ON sc.id=l.candidate_id
        JOIN epidemic_discovery.source_review_cases rc
          ON rc.candidate_version_id=l.candidate_version_id"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.runtime_readiness_v1 AS
        SELECT av.version_num alembic_revision,m.environment,m.database_name,m.production,m.disposable,
          (to_regclass('epidemic_preview.preview_review_cards_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.active_preview_publications_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_dashboard_series_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_lineage_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_safe_source_columns_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_discovery_candidate_links_v1') IS NOT NULL)
          required_objects_present
        FROM epidemic.alembic_version av
        CROSS JOIN epidemic_preview.environment_markers m"""
    )
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT SELECT ON epidemic_preview.preview_safe_source_columns_v1,
            epidemic_preview.preview_discovery_candidate_links_v1,
            epidemic_preview.runtime_readiness_v1 TO epidemicreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
          GRANT SELECT ON epidemic_preview.runtime_readiness_v1 TO epidemic_dashboard_ro;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic_preview.runtime_readiness_v1")
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_discovery_candidate_links_v1")
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_safe_source_columns_v1")

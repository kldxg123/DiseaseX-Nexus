"""Add append-only raw source summaries for Review Console cases.

Revision ID: 0028_review_raw_source_summaries
Revises: 0027_p1_14c_v2_preview_path_grants
"""

from alembic import op

revision = "0028_review_raw_source_summaries"
down_revision = "0027_p1_14c_v2_preview_path_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS epidemic_discovery.source_review_case_raw_source_summaries (
          id uuid PRIMARY KEY,
          review_case_id uuid NOT NULL REFERENCES epidemic_discovery.source_review_cases(id) ON DELETE RESTRICT,
          candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id) ON DELETE RESTRICT,
          summary_version text NOT NULL,
          artifact_sha256 char(64) NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
          summary_input_kind text NOT NULL CHECK (summary_input_kind = 'raw_source_document'),
          candidate_payload_used boolean NOT NULL DEFAULT false CHECK (candidate_payload_used = false),
          original_url text,
          final_url text,
          page_title text,
          publisher text,
          published_at timestamptz,
          retrieved_at timestamptz,
          document_type text NOT NULL,
          summary_status text NOT NULL,
          failure_reason text,
          summary_input_sha256 char(64) CHECK (summary_input_sha256 IS NULL OR summary_input_sha256 ~ '^[0-9a-f]{64}$'),
          summary_json jsonb NOT NULL,
          summary_sha256 char(64) NOT NULL CHECK (summary_sha256 ~ '^[0-9a-f]{64}$'),
          method text NOT NULL,
          model_version text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (summary_status <> 'SUMMARY_READY' OR summary_input_sha256 IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS source_review_case_raw_source_summaries_identity_uq
        ON epidemic_discovery.source_review_case_raw_source_summaries
        (review_case_id, candidate_version_id, summary_version, summary_sha256)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS source_review_case_raw_source_summaries_case_idx
        ON epidemic_discovery.source_review_case_raw_source_summaries
        (review_case_id, created_at DESC)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regprocedure('epidemic_discovery.reject_mutation()') IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgname = 'source_review_case_raw_source_summaries_append_only'
             )
          THEN
            CREATE TRIGGER source_review_case_raw_source_summaries_append_only
            BEFORE UPDATE OR DELETE ON epidemic_discovery.source_review_case_raw_source_summaries
            FOR EACH ROW EXECUTE FUNCTION epidemic_discovery.reject_mutation();
          END IF;
        END$$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW epidemic_discovery.review_case_raw_source_summaries_v1 AS
        SELECT DISTINCT ON (review_case_id)
          id,
          review_case_id,
          candidate_version_id,
          summary_version,
          artifact_sha256,
          summary_input_kind,
          candidate_payload_used,
          original_url,
          final_url,
          page_title,
          publisher,
          published_at,
          retrieved_at,
          document_type,
          summary_status,
          failure_reason,
          summary_input_sha256,
          summary_json,
          summary_sha256,
          method,
          model_version,
          created_at
        FROM epidemic_discovery.source_review_case_raw_source_summaries
        ORDER BY review_case_id, created_at DESC, id DESC
        """
    )
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT SELECT ON epidemic_discovery.source_review_case_raw_source_summaries,
            epidemic_discovery.review_case_raw_source_summaries_v1 TO epidemicreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_app') THEN
          GRANT SELECT, INSERT ON epidemic_discovery.source_review_case_raw_source_summaries TO epidemic_app;
          GRANT SELECT ON epidemic_discovery.review_case_raw_source_summaries_v1 TO epidemic_app;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic_discovery.review_case_raw_source_summaries_v1")
    op.execute("DROP TRIGGER IF EXISTS source_review_case_raw_source_summaries_append_only ON epidemic_discovery.source_review_case_raw_source_summaries")
    op.execute("DROP TABLE IF EXISTS epidemic_discovery.source_review_case_raw_source_summaries")

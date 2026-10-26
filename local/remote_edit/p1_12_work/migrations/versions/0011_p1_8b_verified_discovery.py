# ruff: noqa: E501
"""Add append-only discovery result and fetch ledgers.

Revision ID: 0011_p1_8b_verified_discovery
Revises: 0010_p1_8_daily_updates
"""

from alembic import op

revision = "0011_p1_8b_verified_discovery"
down_revision = "0010_p1_8_daily_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE epidemic_discovery.source_discovery_search_results(
          id uuid PRIMARY KEY,
          discovery_run_id uuid NOT NULL REFERENCES epidemic_discovery.discovery_runs(id),
          provider_id text NOT NULL,
          query_id text NOT NULL,
          query_sha256 char(64) NOT NULL,
          rank integer NOT NULL,
          title text NOT NULL,
          snippet text NOT NULL,
          original_url text NOT NULL,
          canonical_url text NOT NULL,
          canonical_url_sha256 char(64) NOT NULL,
          domain text NOT NULL,
          publisher text NOT NULL,
          stable_dataset_id text,
          source_tier text NOT NULL,
          media_type text,
          landing_page_url text,
          result_sha256 char(64) NOT NULL,
          observed_at timestamptz NOT NULL,
          metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
          disposition text NOT NULL DEFAULT 'DISCOVERED',
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK(rank BETWEEN 1 AND 100),
          CHECK(query_sha256 ~ '^[0-9a-f]{64}$'),
          CHECK(canonical_url_sha256 ~ '^[0-9a-f]{64}$'),
          CHECK(result_sha256 ~ '^[0-9a-f]{64}$'),
          CHECK(disposition IN ('DISCOVERED','DUPLICATE_URL','ALREADY_FORMAL_SOURCE','POLICY_BLOCKED','FETCH_SELECTED')),
          UNIQUE(discovery_run_id,provider_id,query_id,rank,canonical_url_sha256))"""
    )
    op.execute(
        """CREATE TABLE epidemic_discovery.candidate_fetches(
          id uuid PRIMARY KEY,
          discovery_run_id uuid NOT NULL REFERENCES epidemic_discovery.discovery_runs(id),
          search_result_id uuid NOT NULL REFERENCES epidemic_discovery.source_discovery_search_results(id),
          candidate_id uuid REFERENCES epidemic_discovery.source_candidates(id),
          attempted_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          final_url text,
          final_url_sha256 char(64),
          domain text NOT NULL,
          status text NOT NULL,
          http_status integer,
          content_type text,
          byte_size bigint,
          etag text,
          last_modified text,
          elapsed_ms integer,
          artifact_sha256 char(64),
          failure_code text,
          redirect_sha256 jsonb NOT NULL DEFAULT '[]'::jsonb,
          CHECK(status IN ('STARTED','FETCHED','PARSED','REVIEW_READY','BLOCKED','FAILED')),
          CHECK(byte_size IS NULL OR byte_size BETWEEN 0 AND 52428800),
          CHECK(final_url_sha256 IS NULL OR final_url_sha256 ~ '^[0-9a-f]{64}$'),
          CHECK(artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        "ALTER TABLE epidemic_discovery.quarantine_artifacts "
        "ADD COLUMN search_result_id uuid REFERENCES epidemic_discovery.source_discovery_search_results(id), "
        "ADD COLUMN fetch_id uuid UNIQUE REFERENCES epidemic_discovery.candidate_fetches(id)"
    )
    op.execute(
        "CREATE INDEX source_discovery_search_results_run_idx ON "
        "epidemic_discovery.source_discovery_search_results(discovery_run_id,provider_id,query_id,rank)"
    )
    op.execute(
        "CREATE INDEX candidate_fetches_run_idx ON epidemic_discovery.candidate_fetches(discovery_run_id,attempted_at)"
    )
    for table in ("source_discovery_search_results", "candidate_fetches"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE DELETE ON epidemic_discovery.{table} "
            "FOR EACH ROW EXECUTE FUNCTION epidemic_discovery.reject_mutation()"
        )
    op.execute(
        "GRANT SELECT,INSERT ON epidemic_discovery.source_discovery_search_results,"
        "epidemic_discovery.candidate_fetches TO epidemic_app"
    )
    op.execute("GRANT UPDATE ON epidemic_discovery.candidate_fetches TO epidemic_app")
    op.execute(
        "REVOKE DELETE ON epidemic_discovery.source_discovery_search_results,epidemic_discovery.candidate_fetches FROM epidemic_app"
    )
    op.execute(
        """CREATE VIEW epidemic_discovery.review_discovery_search_results_v1 AS
        SELECT r.id AS search_result_id,r.discovery_run_id,r.provider_id,r.query_id,r.rank,r.title,
          r.snippet,r.canonical_url,r.domain,r.publisher,r.stable_dataset_id,r.source_tier,r.media_type,
          r.landing_page_url,r.result_sha256,r.observed_at,r.disposition,r.metadata
        FROM epidemic_discovery.source_discovery_search_results r"""
    )
    op.execute(
        """CREATE VIEW epidemic_discovery.review_candidate_fetches_v1 AS
        SELECT f.id AS fetch_id,f.discovery_run_id,f.search_result_id,f.candidate_id,f.attempted_at,
          f.completed_at,f.final_url,f.domain,f.status,f.http_status,f.content_type,f.byte_size,
          f.etag,f.last_modified,f.elapsed_ms,f.artifact_sha256,f.failure_code,f.redirect_sha256
        FROM epidemic_discovery.candidate_fetches f"""
    )
    op.execute(
        """DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT SELECT ON epidemic_discovery.review_discovery_search_results_v1,
            epidemic_discovery.review_candidate_fetches_v1 TO epidemicreview;
        END IF; END$$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic_discovery.review_candidate_fetches_v1")
    op.execute("DROP VIEW IF EXISTS epidemic_discovery.review_discovery_search_results_v1")
    op.execute("ALTER TABLE epidemic_discovery.quarantine_artifacts DROP COLUMN fetch_id,DROP COLUMN search_result_id")
    op.execute("DROP TABLE epidemic_discovery.candidate_fetches")
    op.execute("DROP TABLE epidemic_discovery.source_discovery_search_results")

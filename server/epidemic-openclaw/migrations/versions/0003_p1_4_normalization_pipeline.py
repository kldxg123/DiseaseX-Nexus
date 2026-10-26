# ruff: noqa: E501
"""Add deterministic WHO normalization pipeline.

Revision ID: 0003_p1_4_normalization_pipeline
Revises: 0002_p1_3_retrieval_control
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_p1_4_normalization_pipeline"
down_revision = "0002_p1_3_retrieval_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""UPDATE epidemic.metric_definitions SET allow_negative=true,min_value=NULL,schema_version=schema_version+1,
      description=CASE metric_code WHEN 'cases_cumulative' THEN 'Cumulative reported cases; WHO retrospective revisions, including decreases or negative values, are preserved and flagged.'
      ELSE 'Cumulative reported deaths; WHO retrospective revisions, including decreases or negative values, are preserved and flagged.' END
      WHERE metric_code IN ('cases_cumulative','deaths_cumulative')""")
    op.create_table(
        "normalization_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_fetch_id", sa.Uuid(), nullable=False),
        sa.Column("normalization_key", sa.String(64), nullable=False),
        sa.Column("transform_version", sa.String(100), nullable=False),
        sa.Column("country_mapping_version", sa.String(100), nullable=False),
        sa.Column("quality_ruleset_version", sa.String(100), nullable=False),
        sa.Column("snapshot_schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result_code", sa.String(100), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("dataset_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("total_source_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("selected_source_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("normalized_observation_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("missing_value_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unmapped_source_code_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("exact_duplicate_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("normalization_key ~ '^[0-9a-f]{64}$'", name="ck_normalization_runs_key_sha"),
        sa.CheckConstraint("status IN ('pending','running','succeeded','failed')", name="ck_normalization_runs_status"),
        sa.CheckConstraint("attempt_number BETWEEN 1 AND 3 AND max_attempts=3", name="ck_normalization_runs_attempts"),
        sa.CheckConstraint(
            "(status='pending' AND claim_token IS NULL AND claimed_at IS NULL AND lease_expires_at IS NULL AND finished_at IS NULL) OR "
            "(status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('succeeded','failed') AND claim_token IS NULL AND lease_expires_at IS NULL AND finished_at IS NOT NULL)",
            name="ck_normalization_runs_state_shape",
        ),
        sa.CheckConstraint(
            "dataset_snapshot_id IS NULL OR (status='succeeded' AND COALESCE(result_code,'')<>'NO_OBSERVATIONS')",
            name="ck_normalization_runs_snapshot_terminal",
        ),
        sa.CheckConstraint(
            "total_source_rows>=0 AND selected_source_rows>=0 AND normalized_observation_count>=0 AND missing_value_count>=0 "
            "AND warning_count>=0 AND unmapped_source_code_count>=0 AND exact_duplicate_count>=0",
            name="ck_normalization_runs_counts_nonnegative",
        ),
        sa.ForeignKeyConstraint(["retrieval_run_id"], ["epidemic.retrieval_runs.id"]),
        sa.ForeignKeyConstraint(["source_fetch_id"], ["epidemic.source_fetches.id"]),
        sa.ForeignKeyConstraint(["dataset_snapshot_id"], ["epidemic.dataset_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalization_key", name="uq_normalization_runs_key"),
        sa.UniqueConstraint(
            "retrieval_run_id",
            "source_fetch_id",
            "transform_version",
            "country_mapping_version",
            "quality_ruleset_version",
            "snapshot_schema_version",
            name="uq_normalization_runs_identity",
        ),
        schema="epidemic",
    )
    op.create_index(
        "ix_normalization_runs_pending_queue",
        "normalization_runs",
        ["created_at"],
        unique=False,
        schema="epidemic",
        postgresql_where=sa.text("status='pending'"),
    )
    op.create_table(
        "normalization_run_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("normalization_run_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("event_code", sa.String(100), nullable=False),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("jsonb_typeof(details)='object'", name="ck_normalization_run_events_details_object"),
        sa.ForeignKeyConstraint(["normalization_run_id"], ["epidemic.normalization_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="epidemic",
    )
    op.create_unique_constraint(
        "uq_dataset_snapshots_query_schema_content",
        "dataset_snapshots",
        ["query_request_id", "snapshot_schema_version", "content_sha256"],
        schema="epidemic",
    )
    op.execute("""CREATE FUNCTION epidemic.protect_normalization_terminal() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF OLD.status='succeeded' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal normalization run is immutable';
      END IF;
      IF OLD.status='failed' AND NEW IS DISTINCT FROM OLD AND NOT (
        NEW.status='pending' AND NEW.attempt_number=OLD.attempt_number+1 AND NEW.attempt_number<=NEW.max_attempts
        AND NEW.claim_token IS NULL AND NEW.claimed_at IS NULL AND NEW.lease_expires_at IS NULL
        AND NEW.started_at IS NULL AND NEW.finished_at IS NULL AND NEW.error_code IS NULL
      ) THEN
        RAISE EXCEPTION 'failed normalization run permits only controlled retry';
      END IF;
      IF NEW.id<>OLD.id OR NEW.normalization_key<>OLD.normalization_key OR NEW.retrieval_run_id<>OLD.retrieval_run_id OR NEW.source_fetch_id<>OLD.source_fetch_id THEN
        RAISE EXCEPTION 'normalization identity is immutable';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute("""CREATE TRIGGER trg_normalization_terminal
    BEFORE UPDATE ON epidemic.normalization_runs FOR EACH ROW
    EXECUTE FUNCTION epidemic.protect_normalization_terminal()""")
    op.execute("""CREATE VIEW epidemic.training_snapshot_observations_v1 AS
    SELECT so.snapshot_id,so.row_number,o.id AS observation_id,o.country_alpha2,c.alpha3 AS country_alpha3,
      d.slug AS disease_slug,o.metric_code,o.value_numeric,m.unit,o.period_start,o.period_end,o.granularity,
      o.dimensions->>'date_basis' AS date_basis,o.as_of,o.revision,q.status AS quality_status,q.score AS quality_score,
      q.flags AS quality_flags,ds.source_key,a.sha256 AS artifact_sha256,o.normalization_version,
      NOT (q.flags ?| ARRAY['LATEST_PERIOD_POSSIBLY_INCOMPLETE','RETROSPECTIVE_NEGATIVE_REVISION','NEGATIVE_CUMULATIVE_VALUE']) AS default_train_eligible
    FROM epidemic.dataset_snapshot_observations so
    JOIN epidemic.dataset_snapshots snap ON snap.id=so.snapshot_id
    JOIN epidemic.normalized_observations o ON o.id=so.observation_id
    JOIN epidemic.countries c ON c.alpha2=o.country_alpha2
    JOIN epidemic.diseases d ON d.id=o.disease_id
    JOIN epidemic.metric_definitions m ON m.metric_code=o.metric_code
    JOIN epidemic.quality_assessments q ON q.observation_id=o.id
    JOIN epidemic.normalization_runs nr ON nr.dataset_snapshot_id=snap.id AND nr.quality_ruleset_version=q.ruleset_version
    JOIN epidemic.retrieval_runs rr ON rr.id=nr.retrieval_run_id
    JOIN epidemic.data_sources ds ON ds.id=rr.source_id
    JOIN epidemic.source_fetches sf ON sf.id=nr.source_fetch_id
    JOIN epidemic.raw_artifacts a ON a.id=sf.artifact_id""")
    op.execute("ALTER TABLE epidemic.normalization_runs OWNER TO epidemic_migrator")
    op.execute("ALTER TABLE epidemic.normalization_run_events OWNER TO epidemic_migrator")
    op.execute("GRANT SELECT,INSERT ON epidemic.normalization_runs TO epidemic_app")
    op.execute(
        "GRANT UPDATE(status,result_code,attempt_number,next_attempt_at,claim_token,claimed_at,lease_expires_at,started_at,finished_at,error_code,dataset_snapshot_id,total_source_rows,selected_source_rows,normalized_observation_count,missing_value_count,warning_count,unmapped_source_code_count,exact_duplicate_count) ON epidemic.normalization_runs TO epidemic_app"
    )
    op.execute("GRANT SELECT,INSERT ON epidemic.normalization_run_events TO epidemic_app")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE epidemic.normalization_run_events_id_seq TO epidemic_app")
    op.execute("GRANT SELECT ON epidemic.training_snapshot_observations_v1 TO epidemic_app")
    for table in (
        "normalization_run_events",
        "normalized_observations",
        "quality_assessments",
        "dataset_snapshots",
        "dataset_snapshot_observations",
    ):
        op.execute(f"REVOKE UPDATE,DELETE,TRUNCATE ON epidemic.{table} FROM epidemic_app")
    op.execute("REVOKE DELETE,TRUNCATE ON epidemic.normalization_runs FROM epidemic_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON epidemic.training_snapshot_observations_v1 FROM epidemic_app")
    op.execute("DROP VIEW epidemic.training_snapshot_observations_v1")
    op.execute("DROP TRIGGER trg_normalization_terminal ON epidemic.normalization_runs")
    op.execute("DROP FUNCTION epidemic.protect_normalization_terminal()")
    op.drop_constraint(
        "uq_dataset_snapshots_query_schema_content", "dataset_snapshots", schema="epidemic", type_="unique"
    )
    op.drop_table("normalization_run_events", schema="epidemic")
    op.drop_index("ix_normalization_runs_pending_queue", table_name="normalization_runs", schema="epidemic")
    op.drop_table("normalization_runs", schema="epidemic")
    op.execute("""UPDATE epidemic.metric_definitions SET allow_negative=false,min_value=0,schema_version=schema_version-1,
      description=CASE metric_code WHEN 'cases_cumulative' THEN 'Cumulative reported cases.' ELSE 'Cumulative reported deaths.' END
      WHERE metric_code IN ('cases_cumulative','deaths_cumulative')""")

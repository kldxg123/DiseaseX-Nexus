"""P1-17A prediction MVP tables.

Revision ID: 0029_p1_17a_prediction_mvp
Revises: 0028_review_raw_source_summaries
Create Date: 2026-07-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029_p1_17a_prediction_mvp"
down_revision = "0028_review_raw_source_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_fingerprint_sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("disease_slug", sa.Text(), nullable=False),
        sa.Column("target_contract", sa.Text(), nullable=False),
        sa.Column("horizon_weeks", sa.Integer(), nullable=False),
        sa.Column("dataset_sha256", sa.Text(), nullable=False),
        sa.Column("split_sha256", sa.Text(), nullable=False),
        sa.Column("model_sha256", sa.Text(), nullable=False),
        sa.Column("feature_schema_sha256", sa.Text(), nullable=False),
        sa.Column("model_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("label_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=True),
        sa.Column("train_end", sa.Date(), nullable=True),
        sa.Column("validation_start", sa.Date(), nullable=True),
        sa.Column("validation_end", sa.Date(), nullable=True),
        sa.Column("test_start", sa.Date(), nullable=True),
        sa.Column("test_end", sa.Date(), nullable=True),
        sa.Column("data_through", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("test_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("test_rowset_sha256", sa.Text(), nullable=True),
        sa.Column("latest_rowset_sha256", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("length(run_fingerprint_sha256)=64", name="prediction_runs_fingerprint_len"),
        sa.CheckConstraint("length(dataset_sha256)=64", name="prediction_runs_dataset_sha_len"),
        sa.CheckConstraint("length(split_sha256)=64", name="prediction_runs_split_sha_len"),
        sa.CheckConstraint("length(model_sha256)=64", name="prediction_runs_model_sha_len"),
        sa.CheckConstraint("length(feature_schema_sha256)=64", name="prediction_runs_feature_sha_len"),
        sa.CheckConstraint("horizon_weeks=1", name="prediction_runs_horizon_one"),
        sa.CheckConstraint("status in ('STAGING','COMPLETE','FAILED')", name="prediction_runs_status_known"),
        schema="epidemic",
    )
    op.create_table(
        "prediction_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Text(), nullable=False),
        sa.Column("series_key", sa.Text(), nullable=False),
        sa.Column("region_id", sa.Text(), nullable=False),
        sa.Column("country_iso3", sa.Text(), nullable=False),
        sa.Column("country_name", sa.Text(), nullable=False),
        sa.Column("surveillance_origin", sa.Text(), nullable=False),
        sa.Column("prediction_origin", sa.Date(), nullable=False),
        sa.Column("target_week", sa.Date(), nullable=False),
        sa.Column("feature_cutoff", sa.Date(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("decision_threshold", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("predicted_alert", sa.Boolean(), nullable=False),
        sa.Column("actual_label", sa.Integer(), nullable=True),
        sa.Column("feature_row_sha256", sa.Text(), nullable=False),
        sa.Column("feature_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["epidemic.prediction_runs.run_id"], ondelete="CASCADE"),
        sa.CheckConstraint("scope in ('TEST','LATEST')", name="prediction_results_scope_known"),
        sa.CheckConstraint("risk_score >= 0 and risk_score <= 1", name="prediction_results_risk_range"),
        sa.CheckConstraint("decision_threshold >= 0 and decision_threshold <= 1", name="prediction_results_threshold_range"),
        sa.CheckConstraint("feature_cutoff < target_week", name="prediction_results_feature_before_target"),
        sa.CheckConstraint("(scope <> 'LATEST') or actual_label is null", name="prediction_results_latest_unlabeled"),
        sa.CheckConstraint("actual_label is null or actual_label in (0,1)", name="prediction_results_label_binary"),
        sa.UniqueConstraint("run_id", "model_key", "scope", "row_id", name="prediction_results_natural_key"),
        schema="epidemic",
    )
    op.create_index("ix_prediction_results_latest_order", "prediction_results", ["run_id", "scope", sa.text("prediction_origin DESC"), sa.text("risk_score DESC"), "country_iso3", "region_id"], schema="epidemic")
    op.create_index("ix_prediction_results_filters", "prediction_results", ["run_id", "model_key", "scope", "country_iso3", "region_id", "surveillance_origin", "target_week"], schema="epidemic")
    op.execute(
        """
        CREATE OR REPLACE VIEW epidemic.prediction_runs_complete_v1 AS
        SELECT * FROM epidemic.prediction_runs WHERE status='COMPLETE';
        CREATE OR REPLACE VIEW epidemic.prediction_results_complete_v1 AS
        SELECT r.* FROM epidemic.prediction_results r
        JOIN epidemic.prediction_runs pr ON pr.run_id=r.run_id
        WHERE pr.status='COMPLETE';
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
            GRANT SELECT ON epidemic.prediction_runs TO epidemic_dashboard_ro;
            GRANT SELECT ON epidemic.prediction_results TO epidemic_dashboard_ro;
            GRANT SELECT ON epidemic.prediction_runs_complete_v1 TO epidemic_dashboard_ro;
            GRANT SELECT ON epidemic.prediction_results_complete_v1 TO epidemic_dashboard_ro;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic.prediction_results_complete_v1")
    op.execute("DROP VIEW IF EXISTS epidemic.prediction_runs_complete_v1")
    op.drop_index("ix_prediction_results_filters", table_name="prediction_results", schema="epidemic")
    op.drop_index("ix_prediction_results_latest_order", table_name="prediction_results", schema="epidemic")
    op.drop_table("prediction_results", schema="epidemic")
    op.drop_table("prediction_runs", schema="epidemic")

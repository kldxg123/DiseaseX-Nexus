"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("robots_txt_url", sa.Text(), nullable=True),
        sa.Column("terms_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=False),
        sa.Column("input_snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_forecast_runs_model_version",
        "forecast_runs",
        ["model_name", "model_version"],
    )
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reviewer_ref", sa.String(length=160), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_decisions_entity",
        "review_decisions",
        ["entity_type", "entity_id"],
    )
    op.create_table(
        "raw_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("parser_version", sa.String(length=80), nullable=False),
        sa.Column("review_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_raw_observations_content_hash",
        "raw_observations",
        ["content_hash"],
    )
    op.create_index(
        "ix_raw_observations_source_retrieved",
        "raw_observations",
        ["source_id", "retrieved_at"],
    )
    op.create_table(
        "forecast_outputs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("forecast_run_id", sa.Integer(), nullable=False),
        sa.Column("disease", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("admin1", sa.String(length=120), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("predicted_value", sa.Float(), nullable=False),
        sa.Column("lower_bound", sa.Float(), nullable=False),
        sa.Column("upper_bound", sa.Float(), nullable=False),
        sa.Column("uncertainty_method", sa.String(length=120), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecast_outputs_run", "forecast_outputs", ["forecast_run_id"])
    op.create_index(
        "ix_forecast_outputs_target",
        "forecast_outputs",
        ["disease", "country", "target_date"],
    )
    op.create_table(
        "normalized_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_observation_id", sa.Integer(), nullable=False),
        sa.Column("disease", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("admin1", sa.String(length=120), nullable=True),
        sa.Column("admin2", sa.String(length=120), nullable=True),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(length=120), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=80), nullable=False),
        sa.Column("source_trace", sa.JSON(), nullable=False),
        sa.Column("quality_flags", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["raw_observation_id"], ["raw_observations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_normalized_observations_disease_date",
        "normalized_observations",
        ["disease", "observed_on"],
    )
    op.create_index(
        "ix_normalized_observations_location_date",
        "normalized_observations",
        ["country", "admin1", "observed_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_normalized_observations_location_date", table_name="normalized_observations")
    op.drop_index("ix_normalized_observations_disease_date", table_name="normalized_observations")
    op.drop_table("normalized_observations")
    op.drop_index("ix_forecast_outputs_target", table_name="forecast_outputs")
    op.drop_index("ix_forecast_outputs_run", table_name="forecast_outputs")
    op.drop_table("forecast_outputs")
    op.drop_index("ix_raw_observations_source_retrieved", table_name="raw_observations")
    op.drop_index("ix_raw_observations_content_hash", table_name="raw_observations")
    op.drop_table("raw_observations")
    op.drop_index("ix_review_decisions_entity", table_name="review_decisions")
    op.drop_table("review_decisions")
    op.drop_index("ix_forecast_runs_model_version", table_name="forecast_runs")
    op.drop_table("forecast_runs")
    op.drop_table("sources")

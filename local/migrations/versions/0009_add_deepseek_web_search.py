"""add deepseek web search persistence tables

Revision ID: 0009_add_deepseek_web_search
Revises: 0008_rename_prediction_feedback_to_human_feedback
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_add_deepseek_web_search"
down_revision = "0008_rename_prediction_feedback_to_human_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deepseek_web_search_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=160), nullable=False),
        sa.Column("disease", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("query_intent", sa.String(length=80), nullable=False),
        sa.Column("search_backend", sa.String(length=80), nullable=False),
        sa.Column("deepseek_model", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("allow_deepseek_api", sa.Boolean(), nullable=False),
        sa.Column("allow_general_web_search", sa.Boolean(), nullable=False),
        sa.Column("allow_page_read", sa.Boolean(), nullable=False),
        sa.Column("search_query_count", sa.Integer(), nullable=False),
        sa.Column("search_request_count", sa.Integer(), nullable=False),
        sa.Column("page_read_request_count", sa.Integer(), nullable=False),
        sa.Column("deepseek_call_count", sa.Integer(), nullable=False),
        sa.Column("records_extracted", sa.Integer(), nullable=False),
        sa.Column("records_persisted", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("safety_summary_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_deepseek_web_search_runs_run_id", "deepseek_web_search_runs", ["run_id"], unique=True)
    op.create_index(
        "ix_deepseek_web_search_runs_disease_country",
        "deepseek_web_search_runs",
        ["disease", "country"],
    )
    op.create_index("ix_deepseek_web_search_runs_created_at", "deepseek_web_search_runs", ["created_at"])

    op.create_table(
        "deepseek_web_search_queries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=160), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_rank", sa.Integer(), nullable=False),
        sa.Column("query_intent", sa.String(length=80), nullable=False),
        sa.Column("generated_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deepseek_web_search_queries_run", "deepseek_web_search_queries", ["run_id"])
    op.create_index(
        "ix_deepseek_web_search_queries_rank",
        "deepseek_web_search_queries",
        ["run_id", "query_rank"],
    )

    op.create_table(
        "deepseek_web_search_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=160), nullable=False),
        sa.Column("query_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("title_summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=200), nullable=False),
        sa.Column("snippet_summary", sa.Text(), nullable=True),
        sa.Column("published_date", sa.String(length=40), nullable=True),
        sa.Column("source_type_hint", sa.String(length=80), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("structured_data_score", sa.Float(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("selected_for_page_read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deepseek_web_search_results_run", "deepseek_web_search_results", ["run_id"])
    op.create_index("ix_deepseek_web_search_results_domain", "deepseek_web_search_results", ["domain"])
    op.create_index(
        "ix_deepseek_web_search_results_score",
        "deepseek_web_search_results",
        ["run_id", "total_score"],
    )

    op.create_table(
        "disease_country_information_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=160), nullable=False),
        sa.Column("disease", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(length=200), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("information_type", sa.String(length=80), nullable=False),
        sa.Column("evidence_type", sa.String(length=80), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("iso_week", sa.String(length=10), nullable=True),
        sa.Column("region_name", sa.String(length=160), nullable=True),
        sa.Column("metric_name", sa.String(length=120), nullable=True),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column("metric_unit", sa.String(length=80), nullable=True),
        sa.Column("text_summary", sa.Text(), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("uncertainty_reason", sa.Text(), nullable=True),
        sa.Column("cleaning_status", sa.String(length=80), nullable=False),
        sa.Column("raw_document_id", sa.Integer(), nullable=True),
        sa.Column("epidemic_event_id", sa.Integer(), nullable=True),
        sa.Column("feature_ready_potential", sa.Boolean(), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_disease_country_records_disease", "disease_country_information_records", ["disease"])
    op.create_index("ix_disease_country_records_country", "disease_country_information_records", ["country"])
    op.create_index(
        "ix_disease_country_records_disease_country",
        "disease_country_information_records",
        ["disease", "country"],
    )
    op.create_index(
        "ix_disease_country_records_date",
        "disease_country_information_records",
        ["disease", "country", "event_date"],
    )
    op.create_index(
        "ix_disease_country_records_week",
        "disease_country_information_records",
        ["disease", "country", "iso_week"],
    )
    op.create_index("ix_disease_country_records_run", "disease_country_information_records", ["run_id"])
    op.create_index("ix_disease_country_records_domain", "disease_country_information_records", ["source_domain"])


def downgrade() -> None:
    op.drop_index("ix_disease_country_records_domain", table_name="disease_country_information_records")
    op.drop_index("ix_disease_country_records_run", table_name="disease_country_information_records")
    op.drop_index("ix_disease_country_records_week", table_name="disease_country_information_records")
    op.drop_index("ix_disease_country_records_date", table_name="disease_country_information_records")
    op.drop_index("ix_disease_country_records_disease_country", table_name="disease_country_information_records")
    op.drop_index("ix_disease_country_records_country", table_name="disease_country_information_records")
    op.drop_index("ix_disease_country_records_disease", table_name="disease_country_information_records")
    op.drop_table("disease_country_information_records")

    op.drop_index("ix_deepseek_web_search_results_score", table_name="deepseek_web_search_results")
    op.drop_index("ix_deepseek_web_search_results_domain", table_name="deepseek_web_search_results")
    op.drop_index("ix_deepseek_web_search_results_run", table_name="deepseek_web_search_results")
    op.drop_table("deepseek_web_search_results")

    op.drop_index("ix_deepseek_web_search_queries_rank", table_name="deepseek_web_search_queries")
    op.drop_index("ix_deepseek_web_search_queries_run", table_name="deepseek_web_search_queries")
    op.drop_table("deepseek_web_search_queries")

    op.drop_index("ix_deepseek_web_search_runs_created_at", table_name="deepseek_web_search_runs")
    op.drop_index("ix_deepseek_web_search_runs_disease_country", table_name="deepseek_web_search_runs")
    op.drop_index("ix_deepseek_web_search_runs_run_id", table_name="deepseek_web_search_runs")
    op.drop_table("deepseek_web_search_runs")

# ruff: noqa: E501
"""Add immutable latest-window evidence and read-only dashboard views.

Revision ID: 0004_p1_6_latest_window_dashboard
Revises: 0003_p1_4_normalization_pipeline
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_p1_6_latest_window_dashboard"
down_revision = "0003_p1_4_normalization_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The required descriptive revision id exceeds Alembic's historical 32-char default.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(32),
        type_=sa.String(64),
        schema="epidemic",
    )
    op.add_column("query_requests", sa.Column("time_window_kind", sa.String(50), nullable=False, server_default="explicit_range"), schema="epidemic")
    op.add_column("query_requests", sa.Column("duration_iso8601", sa.String(20)), schema="epidemic")
    op.add_column("query_requests", sa.Column("source_preference", sa.String(50), nullable=False, server_default="official"), schema="epidemic")
    op.alter_column("query_requests", "start_date", nullable=True, schema="epidemic")
    op.alter_column("query_requests", "end_date", nullable=True, schema="epidemic")
    op.drop_constraint(op.f("ck_query_requests_date_range"), "query_requests", schema="epidemic", type_="check")
    op.create_check_constraint("window_shape", "query_requests", "(time_window_kind='explicit_range' AND start_date IS NOT NULL AND end_date IS NOT NULL AND start_date <= end_date) OR (time_window_kind<>'explicit_range' AND start_date IS NULL AND end_date IS NULL)", schema="epidemic")
    op.create_table(
        "retrieval_window_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("query_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("epidemic.query_requests.id"), nullable=False),
        sa.Column("retrieval_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("epidemic.retrieval_runs.id"), nullable=False),
        sa.Column("time_window_kind", sa.String(50), nullable=False), sa.Column("duration_iso8601", sa.String(20)),
        sa.Column("resolved_start", sa.Date, nullable=False), sa.Column("resolved_end", sa.Date, nullable=False),
        sa.Column("source_declared_max_date", sa.Date, nullable=False), sa.Column("source_data_through", sa.Date, nullable=False),
        sa.Column("cases_new_data_through", sa.Date), sa.Column("deaths_new_data_through", sa.Date),
        sa.Column("cases_cumulative_data_through", sa.Date), sa.Column("deaths_cumulative_data_through", sa.Date),
        sa.Column("source_http_last_modified", sa.String(500)), sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("cadence", sa.String(20), nullable=False), sa.Column("observed_period_count", sa.Integer, nullable=False),
        sa.Column("expected_period_count", sa.Integer, nullable=False), sa.Column("missing_period_count", sa.Integer, nullable=False),
        sa.Column("coverage_ratio", sa.Numeric(8,6), nullable=False), sa.Column("reporting_lag_days", sa.Integer, nullable=False),
        sa.Column("freshness_status", sa.String(20), nullable=False), sa.Column("cases_freshness_status", sa.String(20), nullable=False),
        sa.Column("deaths_freshness_status", sa.String(20), nullable=False), sa.Column("freshness_policy_version", sa.String(100), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False), sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("adapter_version", sa.String(100), nullable=False), sa.Column("contract_version", sa.String(100), nullable=False),
        sa.Column("catalog_sha256", sa.String(64), nullable=False), sa.Column("probe_evidence_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("retrieval_run_id", name="uq_retrieval_window_resolution_run"),
        sa.CheckConstraint("resolved_start < resolved_end", name="ck_retrieval_window_resolutions_resolved_range"),
        sa.CheckConstraint("observed_period_count >= 0 AND expected_period_count >= 0 AND missing_period_count >= 0", name="ck_retrieval_window_resolutions_period_counts"),
        sa.CheckConstraint("coverage_ratio >= 0", name="ck_retrieval_window_resolutions_coverage_nonnegative"),
        sa.CheckConstraint("probe_evidence_sha256 ~ '^[0-9a-f]{64}$'", name="ck_retrieval_window_resolutions_probe_sha"),
        sa.CheckConstraint("catalog_sha256 ~ '^[0-9a-f]{64}$'", name="ck_retrieval_window_resolutions_catalog_sha"), schema="epidemic")
    op.execute("REVOKE ALL ON epidemic.retrieval_window_resolutions FROM PUBLIC")
    op.execute("GRANT SELECT,INSERT ON epidemic.retrieval_window_resolutions TO epidemic_app")
    op.execute("""CREATE VIEW epidemic.dashboard_snapshot_catalog_v1 AS
      SELECT snap.id snapshot_id,q.country_alpha2,c.name country_name,d.slug disease_slug,ds.source_key,
       COALESCE(w.resolved_start,q.start_date) requested_start,COALESCE(w.resolved_end,q.end_date) requested_end,
       w.time_window_kind,w.source_declared_max_date,w.source_data_through,w.cases_new_data_through,w.deaths_new_data_through,
       w.source_http_last_modified,w.retrieved_at,w.reporting_lag_days,w.freshness_status,w.cases_freshness_status,w.deaths_freshness_status,
       w.observed_period_count,w.expected_period_count,w.missing_period_count,w.coverage_ratio,
       snap.created_at snapshot_created_at,snap.snapshot_schema_version,snap.row_count observation_count,snap.content_sha256 snapshot_sha256,
       left(a.sha256,12) artifact_sha12,a.sha256 artifact_sha256,
       COALESCE(snap.feature_spec->>'normalization_contract_version',snap.feature_spec->>'contract_version','unknown') contract_version,
       COALESCE(snap.feature_spec->>'attribution','World Health Organization and underlying reporting countries') attribution,
       COALESCE(snap.feature_spec->>'terms_url',ds.terms_url) terms_url,COALESCE(nr.warning_count,0) warning_count,
       COALESCE((SELECT array_agg(DISTINCT o.granularity ORDER BY o.granularity) FROM epidemic.dataset_snapshot_observations sm JOIN epidemic.normalized_observations o ON o.id=sm.observation_id WHERE sm.snapshot_id=snap.id),'{}'::text[]) granularities
      FROM epidemic.dataset_snapshots snap JOIN epidemic.query_requests q ON q.id=snap.query_request_id
      JOIN epidemic.countries c ON c.alpha2=q.country_alpha2 JOIN epidemic.diseases d ON d.id=q.disease_id
      JOIN epidemic.normalization_runs nr ON nr.dataset_snapshot_id=snap.id AND nr.status='succeeded'
      JOIN epidemic.retrieval_runs rr ON rr.id=nr.retrieval_run_id JOIN epidemic.data_sources ds ON ds.id=rr.source_id
      JOIN epidemic.source_fetches sf ON sf.id=nr.source_fetch_id JOIN epidemic.raw_artifacts a ON a.id=sf.artifact_id
      LEFT JOIN epidemic.retrieval_window_resolutions w ON w.retrieval_run_id=rr.id""")
    op.execute("""CREATE VIEW epidemic.dashboard_observation_series_v1 AS SELECT sm.snapshot_id,sm.row_number,o.period_start,o.period_end,o.granularity,o.metric_code,o.value_numeric,m.unit,o.revision,CASE WHEN q.status<>'accepted' THEN 'fail' WHEN jsonb_array_length(CASE WHEN jsonb_typeof(q.flags)='array' THEN q.flags ELSE '[]'::jsonb END)>0 THEN 'warning' ELSE 'pass' END quality_severity,q.flags quality_flags,NOT (q.flags ?| ARRAY['LATEST_PERIOD_POSSIBLY_INCOMPLETE','RETROSPECTIVE_NEGATIVE_REVISION','NEGATIVE_CUMULATIVE_VALUE','NEGATIVE_REPORTED_REVISION']) default_train_eligible,o.dimensions->>'source_date_type' source_date_type FROM epidemic.dataset_snapshot_observations sm JOIN epidemic.normalized_observations o ON o.id=sm.observation_id JOIN epidemic.metric_definitions m ON m.metric_code=o.metric_code JOIN epidemic.quality_assessments q ON q.observation_id=o.id JOIN epidemic.normalization_runs nr ON nr.dataset_snapshot_id=sm.snapshot_id AND nr.quality_ruleset_version=q.ruleset_version""")
    op.execute("""CREATE VIEW epidemic.dashboard_quality_summary_v1 AS WITH rows AS (SELECT v.*,c.contract_version,CASE WHEN c.contract_version IN ('who-covid19-normalization-v2','who-dengue-xmart-contract-v1') THEN 2 ELSE NULL END expected_metrics FROM epidemic.dashboard_observation_series_v1 v JOIN epidemic.dashboard_snapshot_catalog_v1 c ON c.snapshot_id=v.snapshot_id) SELECT snapshot_id,period_start,period_end,granularity,count(*) FILTER (WHERE quality_severity='pass')::bigint pass_count,count(*) FILTER (WHERE quality_severity='warning')::bigint warning_count,count(*) FILTER (WHERE quality_severity='fail')::bigint fail_count,count(*) FILTER (WHERE default_train_eligible)::bigint default_train_eligible_count,CASE WHEN max(expected_metrics) IS NULL THEN NULL ELSE GREATEST(max(expected_metrics)-count(DISTINCT metric_code) FILTER (WHERE metric_code IN ('cases_new','deaths_new')),0)::bigint END missing_expected_metric_count,CASE WHEN max(expected_metrics) IS NULL THEN 'unknown' ELSE 'known' END expected_metric_status,jsonb_agg(DISTINCT quality_flags) FILTER (WHERE quality_flags <> '[]'::jsonb) quality_flags FROM rows GROUP BY snapshot_id,period_start,period_end,granularity""")
    for view in ("dashboard_snapshot_catalog_v1","dashboard_observation_series_v1","dashboard_quality_summary_v1"):
        op.execute(f"REVOKE ALL ON epidemic.{view} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON epidemic.{view} TO epidemic_app")


def downgrade() -> None:
    for view in ("dashboard_quality_summary_v1","dashboard_observation_series_v1","dashboard_snapshot_catalog_v1"):
        op.execute(f"DROP VIEW epidemic.{view}")
    op.drop_table("retrieval_window_resolutions", schema="epidemic")
    op.drop_constraint(op.f("ck_query_requests_window_shape"), "query_requests", schema="epidemic", type_="check")
    op.alter_column("query_requests", "start_date", nullable=False, schema="epidemic")
    op.alter_column("query_requests", "end_date", nullable=False, schema="epidemic")
    op.create_check_constraint("date_range", "query_requests", "start_date <= end_date", schema="epidemic")
    op.drop_column("query_requests", "source_preference", schema="epidemic")
    op.drop_column("query_requests", "duration_iso8601", schema="epidemic")
    op.drop_column("query_requests", "time_window_kind", schema="epidemic")

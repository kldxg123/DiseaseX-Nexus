"""Add reliable daily update schedules, leased runs and read-only status.

Revision ID: 0010_p1_8_daily_updates
Revises: 0009_p1_7a_keyfree_discovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_p1_8_daily_updates"
down_revision: str | None = "0009_p1_7a_keyfree_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATES = (
    "QUEUED",
    "CLAIMED",
    "FETCHING",
    "NORMALIZING",
    "SNAPSHOTTING",
    "SUCCEEDED_CHANGED",
    "SUCCEEDED_NO_CHANGE",
    "PARTIAL",
    "FAILED_RETRYABLE",
    "FAILED_TERMINAL",
    "CANCELLED_SOURCE_DISABLED",
)


def upgrade() -> None:
    op.create_table(
        "daily_update_schedules",
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_name", sa.String(200), nullable=False),
        sa.Column("data_source_id", sa.Uuid(), nullable=False),
        sa.Column("query_request_id", sa.Uuid(), nullable=False),
        sa.Column("country_alpha2", sa.String(2), nullable=False),
        sa.Column("disease_id", sa.Uuid(), nullable=False),
        sa.Column("query_fingerprint", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(100), nullable=False),
        sa.Column("normalization_contract_version", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("local_execution_time", sa.Time(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("catch_up_policy", sa.String(50), nullable=False),
        sa.Column("revision_lookback", postgresql.JSONB(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_source_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_data_through", sa.Date(), nullable=True),
        sa.Column("normalized_data_through", sa.Date(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint("query_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_daily_schedule_query_sha"),
        sa.CheckConstraint("jsonb_typeof(revision_lookback)='object'", name="ck_daily_schedule_lookback_object"),
        sa.CheckConstraint("catch_up_policy IN ('coalesce_latest')", name="ck_daily_schedule_catchup"),
        sa.CheckConstraint("consecutive_failures>=0 AND state_version>=1", name="ck_daily_schedule_counters"),
        sa.ForeignKeyConstraint(["data_source_id"], ["epidemic.data_sources.id"]),
        sa.ForeignKeyConstraint(["query_request_id"], ["epidemic.query_requests.id"]),
        sa.ForeignKeyConstraint(["country_alpha2"], ["epidemic.countries.alpha2"]),
        sa.ForeignKeyConstraint(["disease_id"], ["epidemic.diseases.id"]),
        sa.PrimaryKeyConstraint("schedule_id"),
        sa.UniqueConstraint("schedule_name", name="uq_daily_schedule_name"),
        sa.UniqueConstraint("data_source_id", "query_request_id", name="uq_daily_schedule_target"),
        schema="epidemic",
    )
    op.create_index(
        "ix_daily_schedule_due",
        "daily_update_schedules",
        ["next_due_at"],
        schema="epidemic",
        postgresql_where=sa.text("enabled"),
    )
    op.create_table(
        "daily_update_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("logical_date", sa.Date(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("missed_intervals", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("retry_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_owner", sa.String(200), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieval_run_id", sa.Uuid(), nullable=True),
        sa.Column("normalization_run_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("source_changed", sa.Boolean(), nullable=True),
        sa.Column("observations_added", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("observations_revised", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_excluded", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("final_reason_code", sa.String(120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint("trigger_type IN ('SCHEDULED','STARTUP_CATCH_UP','MANUAL')", name="ck_daily_run_trigger"),
        sa.CheckConstraint("state IN (" + ",".join(f"'{x}'" for x in RUN_STATES) + ")", name="ck_daily_run_state"),
        sa.CheckConstraint(
            "missed_intervals>=0 AND retry_count>=0 AND max_retries>=0 "
            "AND retry_count<=max_retries AND observations_added>=0 "
            "AND observations_revised>=0 AND rows_excluded>=0",
            name="ck_daily_run_counts",
        ),
        sa.CheckConstraint(
            "(claim_token IS NULL AND claim_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(claim_token IS NOT NULL AND claim_owner IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_daily_run_claim_shape",
        ),
        sa.CheckConstraint("finished_at IS NULL OR started_at IS NOT NULL", name="ck_daily_run_finished_started"),
        sa.ForeignKeyConstraint(["schedule_id"], ["epidemic.daily_update_schedules.schedule_id"]),
        sa.ForeignKeyConstraint(["retrieval_run_id"], ["epidemic.retrieval_runs.id"]),
        sa.ForeignKeyConstraint(["normalization_run_id"], ["epidemic.normalization_runs.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["epidemic.dataset_snapshots.id"]),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("schedule_id", "logical_date", name="uq_daily_run_logical_date"),
        schema="epidemic",
    )
    op.create_index(
        "ix_daily_run_coordinator_queue",
        "daily_update_runs",
        ["created_at"],
        schema="epidemic",
        postgresql_where=sa.text(
            "state IN ('QUEUED','CLAIMED','FETCHING','NORMALIZING','SNAPSHOTTING','FAILED_RETRYABLE')"
        ),
    )
    op.create_table(
        "daily_update_run_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.String(40), nullable=True),
        sa.Column("to_state", sa.String(40), nullable=False),
        sa.Column("event_code", sa.String(120), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint("jsonb_typeof(details)='object'", name="ck_daily_event_details_object"),
        sa.ForeignKeyConstraint(["run_id"], ["epidemic.daily_update_runs.run_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        schema="epidemic",
    )
    op.execute("""CREATE FUNCTION epidemic.reject_daily_event_mutation() RETURNS trigger
      LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'daily update events are append-only'; END$$""")
    op.execute("""CREATE TRIGGER trg_daily_event_append_only BEFORE UPDATE OR DELETE
      ON epidemic.daily_update_run_events FOR EACH ROW EXECUTE FUNCTION epidemic.reject_daily_event_mutation()""")
    op.execute("""CREATE FUNCTION epidemic.reconcile_daily_updates(p_trigger text,p_now timestamptz)
      RETURNS TABLE(run_id uuid,schedule_id uuid,created boolean,missed_intervals integer)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic AS $$
      DECLARE s epidemic.daily_update_schedules%ROWTYPE; rid uuid; made boolean; missed integer;
        local_date date; due_date date; next_local_date date; trigger_value text;
      BEGIN
        IF p_trigger NOT IN ('openclaw-cron','startup') THEN RAISE EXCEPTION 'INVALID_DAILY_TRIGGER'; END IF;
        PERFORM pg_advisory_xact_lock(hashtext('epidemic-daily-reconcile-v1'));
        FOR s IN SELECT * FROM epidemic.daily_update_schedules
          WHERE enabled AND next_due_at<=p_now
          ORDER BY next_due_at,schedule_id FOR UPDATE LOOP
          local_date=(p_now AT TIME ZONE s.timezone)::date;
          due_date=(s.next_due_at AT TIME ZONE s.timezone)::date;
          missed=GREATEST(1,local_date-due_date+
            CASE WHEN (p_now AT TIME ZONE s.timezone)::time>=s.local_execution_time THEN 1 ELSE 0 END);
          trigger_value=CASE WHEN p_trigger='startup' THEN 'STARTUP_CATCH_UP' ELSE 'SCHEDULED' END;
          rid=NULL; made=false;
          INSERT INTO epidemic.daily_update_runs(
            run_id,schedule_id,logical_date,scheduled_for,trigger_type,state,missed_intervals)
            VALUES(gen_random_uuid(),s.schedule_id,local_date,s.next_due_at,trigger_value,'QUEUED',missed)
            ON CONFLICT ON CONSTRAINT uq_daily_run_logical_date DO NOTHING
            RETURNING daily_update_runs.run_id INTO rid;
          IF rid IS NULL THEN
            SELECT r.run_id INTO rid FROM epidemic.daily_update_runs r
              WHERE r.schedule_id=s.schedule_id AND r.logical_date=local_date;
          ELSE made=true; INSERT INTO epidemic.daily_update_run_events(run_id,from_state,to_state,event_code,details)
            VALUES(rid,NULL,'QUEUED','DAILY_UPDATE_QUEUED',
              jsonb_build_object('trigger',p_trigger,'missed_intervals',missed));
          END IF;
          next_local_date=CASE
            WHEN (p_now AT TIME ZONE s.timezone)::time < s.local_execution_time
            THEN local_date ELSE local_date+1 END;
          UPDATE epidemic.daily_update_schedules
            SET next_due_at=(next_local_date+s.local_execution_time) AT TIME ZONE s.timezone,
            updated_at=p_now,state_version=state_version+1 WHERE daily_update_schedules.schedule_id=s.schedule_id;
          run_id=rid;schedule_id=s.schedule_id;created=made;missed_intervals=missed;RETURN NEXT;
        END LOOP;
      END$$""")
    op.execute("""CREATE FUNCTION epidemic.submit_manual_daily_update(p_schedule uuid,p_now timestamptz)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic AS $$
      DECLARE s epidemic.daily_update_schedules%ROWTYPE;rid uuid;local_date date;
      BEGIN
        SELECT * INTO s FROM epidemic.daily_update_schedules WHERE schedule_id=p_schedule AND enabled FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'DAILY_SCHEDULE_NOT_AVAILABLE'; END IF;
        local_date=(p_now AT TIME ZONE s.timezone)::date;
        INSERT INTO epidemic.daily_update_runs(
          run_id,schedule_id,logical_date,scheduled_for,trigger_type,state,missed_intervals)
          VALUES(gen_random_uuid(),s.schedule_id,local_date,p_now,'MANUAL','QUEUED',0)
          ON CONFLICT ON CONSTRAINT uq_daily_run_logical_date DO NOTHING RETURNING run_id INTO rid;
        IF rid IS NULL THEN
          SELECT r.run_id INTO rid FROM epidemic.daily_update_runs r
            WHERE r.schedule_id=s.schedule_id AND r.logical_date=local_date;
        ELSE INSERT INTO epidemic.daily_update_run_events(run_id,from_state,to_state,event_code,details)
          VALUES(rid,NULL,'QUEUED','DAILY_UPDATE_QUEUED',jsonb_build_object('trigger','manual')); END IF;
        RETURN rid;
      END$$""")
    op.execute("""CREATE VIEW epidemic.daily_update_status_v1 AS
      SELECT s.schedule_id,s.schedule_name,s.enabled,s.country_alpha2,c.alpha3 country_alpha3,c.name country_name,
        d.slug disease_slug,d.canonical_name disease_name,ds.source_key,ds.display_name source_name,
        s.timezone,s.cron_expression,s.local_execution_time,s.next_due_at,s.last_checked_at,s.last_success_at,
        s.last_source_change_at,s.source_data_through,s.normalized_data_through,s.consecutive_failures,
        r.run_id,r.logical_date,r.trigger_type,r.state,r.missed_intervals,r.retry_count,r.max_retries,
        r.retry_not_before,r.source_changed,r.observations_added,
        r.observations_revised,r.rows_excluded,r.final_reason_code,r.started_at,r.finished_at,r.snapshot_id,
        CASE WHEN s.enabled AND s.next_due_at<now() THEN 'DELAYED' ELSE COALESCE(r.state,'NOT_RUN') END display_state
      FROM epidemic.daily_update_schedules s JOIN epidemic.data_sources ds ON ds.id=s.data_source_id
      JOIN epidemic.countries c ON c.alpha2=s.country_alpha2 JOIN epidemic.diseases d ON d.id=s.disease_id
      LEFT JOIN LATERAL(
        SELECT x.* FROM epidemic.daily_update_runs x
        WHERE x.schedule_id=s.schedule_id ORDER BY x.created_at DESC LIMIT 1
      ) r ON true""")
    for signature in ("reconcile_daily_updates(text,timestamptz)", "submit_manual_daily_update(uuid,timestamptz)"):
        op.execute(f"REVOKE ALL ON FUNCTION epidemic.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION epidemic.{signature} TO epidemic_app")
    op.execute(
        "REVOKE ALL ON epidemic.daily_update_schedules,epidemic.daily_update_runs,"
        "epidemic.daily_update_run_events FROM PUBLIC"
    )
    op.execute(
        "GRANT SELECT ON epidemic.daily_update_schedules,epidemic.daily_update_runs,"
        "epidemic.daily_update_run_events TO epidemic_app"
    )
    op.execute(
        "GRANT UPDATE(next_due_at,last_checked_at,last_success_at,last_source_change_at,"
        "source_data_through,normalized_data_through,consecutive_failures,updated_at,state_version) "
        "ON epidemic.daily_update_schedules TO epidemic_app"
    )
    op.execute(
        "GRANT UPDATE(state,retry_count,retry_not_before,claim_owner,claim_token,claimed_at,"
        "lease_expires_at,heartbeat_at,retrieval_run_id,normalization_run_id,snapshot_id,"
        "source_changed,observations_added,observations_revised,rows_excluded,final_reason_code,"
        "started_at,finished_at) ON epidemic.daily_update_runs TO epidemic_app"
    )
    op.execute("GRANT INSERT ON epidemic.daily_update_run_events TO epidemic_app")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE epidemic.daily_update_run_events_event_id_seq TO epidemic_app")
    op.execute("REVOKE ALL ON epidemic.daily_update_status_v1 FROM PUBLIC")
    op.execute("GRANT SELECT ON epidemic.daily_update_status_v1 TO epidemic_app,epidemic_dashboard_ro")
    op.execute("""DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
      GRANT USAGE ON SCHEMA epidemic TO epidemicreview;
      GRANT SELECT ON epidemic.daily_update_status_v1 TO epidemicreview;
    END IF; END$$""")


def downgrade() -> None:
    op.execute("""DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
      REVOKE SELECT ON epidemic.daily_update_status_v1 FROM epidemicreview;
      REVOKE USAGE ON SCHEMA epidemic FROM epidemicreview;
    END IF; END$$""")
    op.execute("DROP VIEW epidemic.daily_update_status_v1")
    op.execute("DROP FUNCTION epidemic.submit_manual_daily_update(uuid,timestamptz)")
    op.execute("DROP FUNCTION epidemic.reconcile_daily_updates(text,timestamptz)")
    op.execute("DROP TRIGGER trg_daily_event_append_only ON epidemic.daily_update_run_events")
    op.execute("DROP FUNCTION epidemic.reject_daily_event_mutation()")
    op.drop_table("daily_update_run_events", schema="epidemic")
    op.drop_table("daily_update_runs", schema="epidemic")
    op.drop_table("daily_update_schedules", schema="epidemic")

# ruff: noqa: E501
"""Add leased discovery queue observability and review read models.

Revision ID: 0007_p1_7a_discovery_observability
Revises: 0006_p1_7a_ui_read_models
"""

from alembic import op

revision = "0007_p1_7a_discovery_observability"
down_revision = "0006_p1_7a_ui_read_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE epidemic_discovery.discovery_runs DROP CONSTRAINT discovery_runs_state_check")
    op.execute("""ALTER TABLE epidemic_discovery.discovery_runs ADD CONSTRAINT discovery_runs_state_check CHECK(state IN (
      'QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','REVIEW_READY','NO_CANDIDATE',
      'DUPLICATE_ONLY','POLICY_BLOCKED','SECURITY_BLOCKED','LICENSE_BLOCKED','UNSUPPORTED_FORMAT',
      'INVALID_REQUEST','FAILED_RETRYABLE','FAILED_TERMINAL'))""")
    op.execute("""ALTER TABLE epidemic_discovery.discovery_runs
      ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
      ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now(),
      ADD COLUMN claim_token uuid,
      ADD COLUMN claim_owner text,
      ADD COLUMN lease_expires_at timestamptz,
      ADD COLUMN heartbeat_at timestamptz,
      ADD COLUMN last_error_code text,
      ADD COLUMN last_error_detail text,
      ADD COLUMN terminal_reason_code text,
      ADD COLUMN progress_json jsonb NOT NULL DEFAULT jsonb_build_object(
        'plan_steps',0,'directories',0,'queries',0,'raw_results',0,
        'deduplicated',0,'qualified',0,'blocked',0),
      ADD COLUMN request_validated_at timestamptz,
      ADD COLUMN completed_at timestamptz,
      ADD CONSTRAINT ck_discovery_claim_complete CHECK(
        (claim_token IS NULL AND claim_owner IS NULL AND lease_expires_at IS NULL) OR
        (claim_token IS NOT NULL AND claim_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
      ADD CONSTRAINT ck_discovery_progress_object CHECK(jsonb_typeof(progress_json)='object')""")
    op.execute(
        "CREATE INDEX ix_discovery_runs_claimable ON epidemic_discovery.discovery_runs(next_attempt_at,created_at) WHERE state IN ('QUEUED','FAILED_RETRYABLE','DISCOVERING','CANDIDATES_FOUND','PROFILING')"
    )
    op.execute("""ALTER TABLE epidemic_discovery.discovery_run_events
      ADD COLUMN requires_user_action boolean NOT NULL DEFAULT false""")
    op.execute("""CREATE TABLE epidemic_discovery.discovery_worker_heartbeats(
      worker_id text PRIMARY KEY,worker_version text NOT NULL,started_at timestamptz NOT NULL,
      heartbeat_at timestamptz NOT NULL,current_run_id uuid REFERENCES epidemic_discovery.discovery_runs(id),
      last_error_code text,process_id integer NOT NULL CHECK(process_id>0))""")

    op.execute("""CREATE OR REPLACE FUNCTION epidemic_discovery.submit_structured_discovery(
      p_country_iso3 text,p_disease_id uuid,p_metrics text[],p_time_window text,p_tiers text[],
      p_languages text[],p_max_candidates integer) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE req jsonb; digest_sha text; run_id uuid; event_detail jsonb;
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM epidemic.countries WHERE alpha3=p_country_iso3 AND active) OR
          NOT EXISTS(SELECT 1 FROM epidemic.diseases WHERE id=p_disease_id AND active) THEN RAISE EXCEPTION 'INVALID_CATALOG_SELECTION'; END IF;
        IF p_metrics IS NULL OR cardinality(p_metrics)=0 OR NOT (p_metrics <@ ARRAY['cases_new','deaths_new','incidence_rate']::text[]) OR
          p_time_window NOT IN ('latest_rolling_year','latest_complete_calendar_year','current_year_to_latest_period') OR
          p_tiers IS NULL OR cardinality(p_tiers)=0 OR NOT (p_tiers <@ ARRAY['NATIONAL_HEALTH','GOVERNMENT_OPEN_DATA','REGIONAL_HEALTH','PUBLIC_HEALTH_RESEARCH']::text[]) OR
          p_max_candidates NOT BETWEEN 1 AND 20 THEN RAISE EXCEPTION 'INVALID_DISCOVERY_REQUEST'; END IF;
        req=jsonb_build_object('country_iso3',p_country_iso3,'disease_id',p_disease_id,'desired_metrics',p_metrics,
          'time_window_kind',p_time_window,'preferred_source_tiers',p_tiers,'language_preferences',COALESCE(p_languages,ARRAY['en']),
          'max_candidates',p_max_candidates);
        digest_sha=encode(sha256(convert_to(req::text,'UTF8')),'hex');
        SELECT id INTO run_id FROM epidemic_discovery.discovery_runs WHERE request_sha256=digest_sha;
        IF run_id IS NOT NULL THEN RETURN run_id; END IF;
        run_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',req,digest_sha,'source-discovery-policy-v1',
          jsonb_build_object('queries',5,'results_per_query',20,'deep_candidates',5,'full_downloads',2,
          'http_requests',100,'file_bytes',52428800,'task_bytes',262144000,'global_concurrency',4,
          'domain_concurrency',1,'duration_seconds',900))
          ON CONFLICT(request_sha256) DO NOTHING;
        SELECT id INTO run_id FROM epidemic_discovery.discovery_runs WHERE request_sha256=digest_sha;
        IF NOT EXISTS(SELECT 1 FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=run_id) THEN
          event_detail=jsonb_build_object('origin','review_console_structured_form');
          INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
            VALUES(gen_random_uuid(),run_id,NULL,'QUEUED','DISCOVERY_QUEUED',event_detail,
            encode(sha256(convert_to(run_id::text||event_detail::text||clock_timestamp()::text,'UTF8')),'hex'));
        END IF;
        PERFORM pg_notify('epidemic_discovery_queue',run_id::text);
        RETURN run_id;
      END$$""")

    op.execute("""CREATE FUNCTION epidemic_discovery.touch_discovery_worker(
      p_worker text,p_version text,p_started_at timestamptz,p_run uuid,p_error text,p_pid integer) RETURNS void
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      BEGIN
        IF p_worker IS NULL OR length(p_worker)>160 OR p_pid<=0 THEN RAISE EXCEPTION 'INVALID_WORKER'; END IF;
        INSERT INTO epidemic_discovery.discovery_worker_heartbeats(worker_id,worker_version,started_at,heartbeat_at,current_run_id,last_error_code,process_id)
          VALUES(p_worker,p_version,p_started_at,now(),p_run,p_error,p_pid)
        ON CONFLICT(worker_id) DO UPDATE SET worker_version=EXCLUDED.worker_version,heartbeat_at=now(),
          current_run_id=EXCLUDED.current_run_id,last_error_code=EXCLUDED.last_error_code,process_id=EXCLUDED.process_id;
      END$$""")

    op.execute("""CREATE FUNCTION epidemic_discovery.is_discovery_worker_healthy(p_pid integer) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      SELECT EXISTS(SELECT 1 FROM epidemic_discovery.discovery_worker_heartbeats
        WHERE process_id=p_pid AND heartbeat_at>now()-interval '10 seconds')
      $$""")

    op.execute("""CREATE FUNCTION epidemic_discovery.claim_discovery_run(
      p_worker text,p_token uuid,p_lease_seconds integer) RETURNS TABLE(
      run_id uuid,request_payload jsonb,claim_token uuid,attempt_number integer,budget jsonb)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE candidate epidemic_discovery.discovery_runs%ROWTYPE; old_state text; missing text[];
      BEGIN
        IF p_token IS NULL OR p_worker IS NULL OR p_lease_seconds NOT BETWEEN 10 AND 300 THEN RAISE EXCEPTION 'INVALID_CLAIM'; END IF;
        LOOP
          SELECT dr.* INTO candidate FROM epidemic_discovery.discovery_runs dr
          WHERE ((dr.state IN ('QUEUED','FAILED_RETRYABLE') AND dr.next_attempt_at<=now()) OR
                 (dr.state IN ('DISCOVERING','CANDIDATES_FOUND','PROFILING') AND dr.lease_expires_at<now()))
          ORDER BY dr.next_attempt_at,dr.created_at,dr.id FOR UPDATE SKIP LOCKED LIMIT 1;
          IF NOT FOUND THEN RETURN; END IF;
          missing=ARRAY(SELECT key FROM unnest(ARRAY['country_iso3','disease_id','desired_metrics','time_window_kind','preferred_source_tiers','language_preferences','max_candidates']) key WHERE NOT (candidate.request_json ? key));
          IF cardinality(missing)>0 OR jsonb_typeof(candidate.request_json->'desired_metrics')<>'array' OR jsonb_array_length(candidate.request_json->'desired_metrics')=0 THEN
            UPDATE epidemic_discovery.discovery_runs SET state='INVALID_REQUEST',state_version=state_version+1,
              terminal_reason_code='MISSING_OR_INVALID_REQUEST_FIELDS',last_error_code='INVALID_REQUEST',
              last_error_detail=array_to_string(missing,','),completed_at=now(),updated_at=now(),claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL
              WHERE id=candidate.id;
            INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256,requires_user_action)
              VALUES(gen_random_uuid(),candidate.id,candidate.state,'INVALID_REQUEST','REQUEST_INVALID',jsonb_build_object('missing_fields',missing),
              encode(sha256(convert_to(candidate.id::text||clock_timestamp()::text||'REQUEST_INVALID','UTF8')),'hex'),true);
            CONTINUE;
          END IF;
          old_state=candidate.state;
          UPDATE epidemic_discovery.discovery_runs SET state='DISCOVERING',state_version=state_version+1,
            attempt_count=attempt_count+1,claim_token=p_token,claim_owner=p_worker,heartbeat_at=now(),
            lease_expires_at=now()+make_interval(secs=>p_lease_seconds),request_validated_at=COALESCE(request_validated_at,now()),
            last_error_code=NULL,last_error_detail=NULL,updated_at=now() WHERE id=candidate.id;
          INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
            VALUES(gen_random_uuid(),candidate.id,old_state,'DISCOVERING',CASE WHEN old_state IN ('DISCOVERING','CANDIDATES_FOUND','PROFILING') THEN 'STALE_CLAIM_RECOVERED' ELSE 'WORKER_CLAIMED' END,
            jsonb_build_object('worker',p_worker,'attempt',candidate.attempt_count+1,'lease_seconds',p_lease_seconds),
            encode(sha256(convert_to(candidate.id::text||p_token::text||clock_timestamp()::text,'UTF8')),'hex'));
          RETURN QUERY SELECT candidate.id,candidate.request_json,p_token,candidate.attempt_count+1,candidate.budget_json;
          RETURN;
        END LOOP;
      END$$""")

    op.execute("""CREATE FUNCTION epidemic_discovery.heartbeat_discovery_claim(
      p_run uuid,p_token uuid,p_worker text,p_lease_seconds integer) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      BEGIN
        UPDATE epidemic_discovery.discovery_runs SET heartbeat_at=now(),lease_expires_at=now()+make_interval(secs=>p_lease_seconds),updated_at=now()
          WHERE id=p_run AND claim_token=p_token AND claim_owner=p_worker AND state IN ('DISCOVERING','CANDIDATES_FOUND','PROFILING');
        RETURN FOUND;
      END$$""")

    op.execute("""CREATE FUNCTION epidemic_discovery.transition_discovery_claim(
      p_run uuid,p_token uuid,p_state text,p_code text,p_details jsonb,p_progress jsonb) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE old_state text;
      BEGIN
        IF p_state NOT IN ('DISCOVERING','CANDIDATES_FOUND','PROFILING') OR jsonb_typeof(COALESCE(p_progress,'{}'))<>'object' THEN RAISE EXCEPTION 'INVALID_TRANSITION'; END IF;
        SELECT state INTO old_state FROM epidemic_discovery.discovery_runs WHERE id=p_run AND claim_token=p_token FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        UPDATE epidemic_discovery.discovery_runs SET state=p_state,state_version=state_version+1,progress_json=progress_json||COALESCE(p_progress,'{}'),heartbeat_at=now(),updated_at=now() WHERE id=p_run;
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
          VALUES(gen_random_uuid(),p_run,old_state,p_state,p_code,COALESCE(p_details,'{}'),encode(sha256(convert_to(p_run::text||p_token::text||p_code||clock_timestamp()::text,'UTF8')),'hex'));
        RETURN true;
      END$$""")

    op.execute("""CREATE FUNCTION epidemic_discovery.finish_discovery_claim(
      p_run uuid,p_token uuid,p_state text,p_reason text,p_details jsonb,p_progress jsonb,p_retry_seconds integer DEFAULT 0) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE old_state text; terminal boolean;
      BEGIN
        IF p_state NOT IN ('REVIEW_READY','NO_CANDIDATE','DUPLICATE_ONLY','POLICY_BLOCKED','SECURITY_BLOCKED','LICENSE_BLOCKED','UNSUPPORTED_FORMAT','INVALID_REQUEST','FAILED_RETRYABLE','FAILED_TERMINAL') THEN RAISE EXCEPTION 'INVALID_TERMINAL_STATE'; END IF;
        SELECT state INTO old_state FROM epidemic_discovery.discovery_runs WHERE id=p_run AND claim_token=p_token FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        terminal=p_state<>'FAILED_RETRYABLE';
        UPDATE epidemic_discovery.discovery_runs SET state=p_state,state_version=state_version+1,
          terminal_reason_code=CASE WHEN terminal THEN p_reason ELSE NULL END,last_error_code=CASE WHEN p_state LIKE 'FAILED_%' THEN p_reason ELSE NULL END,
          last_error_detail=CASE WHEN p_state LIKE 'FAILED_%' THEN left(COALESCE(p_details->>'message',''),1000) ELSE NULL END,
          progress_json=progress_json||COALESCE(p_progress,'{}'),next_attempt_at=CASE WHEN p_state='FAILED_RETRYABLE' THEN now()+make_interval(secs=>greatest(1,p_retry_seconds)) ELSE next_attempt_at END,
          completed_at=CASE WHEN terminal THEN now() ELSE NULL END,updated_at=now(),claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL WHERE id=p_run;
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256,requires_user_action)
          VALUES(gen_random_uuid(),p_run,old_state,p_state,p_reason,COALESCE(p_details,'{}'),
          encode(sha256(convert_to(p_run::text||p_token::text||p_reason||clock_timestamp()::text,'UTF8')),'hex'),p_state='INVALID_REQUEST');
        RETURN true;
      END$$""")

    op.execute("""CREATE VIEW epidemic_discovery.review_discovery_runs_v3 AS
      SELECT dr.id discovery_run_id,dr.state,dr.state_version,dr.request_json,dr.policy_version,dr.budget_json,
        dr.attempt_count,dr.next_attempt_at,dr.claim_owner,dr.lease_expires_at,dr.heartbeat_at,dr.last_error_code,
        dr.last_error_detail,dr.terminal_reason_code,dr.progress_json,dr.created_at,dr.updated_at,dr.completed_at,
        c.alpha2 country_iso2,c.alpha3 country_iso3,c.name country_name_en,
        COALESCE((SELECT ca.alias FROM epidemic.country_aliases ca WHERE ca.country_alpha2=c.alpha2 AND ca.locale LIKE 'zh%' ORDER BY ca.alias_type,ca.id LIMIT 1),c.name) country_name_zh,
        d.id disease_id,d.slug disease_code,d.canonical_name disease_name_en,
        COALESCE((SELECT da.alias FROM epidemic.disease_aliases da WHERE da.disease_id=d.id AND da.language_tag LIKE 'zh%' ORDER BY da.alias_type,da.id LIMIT 1),d.canonical_name) disease_name_zh,
        (SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id) candidate_count,
        (SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status='REVIEW_READY') qualified_count,
        (SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status<>'REVIEW_READY') blocked_count,
        le.event_code last_event_code,le.created_at last_event_at,le.requires_user_action,
        COALESCE((SELECT bool_or(wh.heartbeat_at>now()-interval '15 seconds') FROM epidemic_discovery.discovery_worker_heartbeats wh),false) worker_healthy,
        extract(epoch FROM (COALESCE(dr.completed_at,now())-dr.created_at))::bigint elapsed_seconds
      FROM epidemic_discovery.discovery_runs dr
      LEFT JOIN epidemic.countries c ON c.alpha3=dr.request_json->>'country_iso3'
      LEFT JOIN epidemic.diseases d ON d.id=CASE
        WHEN dr.request_json->>'disease_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN (dr.request_json->>'disease_id')::uuid ELSE NULL END
      LEFT JOIN LATERAL (SELECT e.event_code,e.created_at,e.requires_user_action FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=dr.id ORDER BY e.created_at DESC,e.id DESC LIMIT 1) le ON true""")
    op.execute("""CREATE VIEW epidemic_discovery.review_discovery_events_v3 AS
      SELECT e.id event_id,e.discovery_run_id,e.from_state,e.to_state,e.event_code,e.details,e.requires_user_action,e.created_at
      FROM epidemic_discovery.discovery_run_events e""")
    op.execute("""CREATE VIEW epidemic_discovery.review_preview_rows_v2 AS
      SELECT rc.id review_case_id,pr.sample_kind,pr.row_ordinal,pr.raw_json,pr.sanitized_json,pr.row_errors,pr.row_sha256,
        count(*) OVER(PARTITION BY rc.id) total_rows
      FROM epidemic_discovery.source_preview_rows pr
      JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=pr.candidate_version_id""")

    for signature in (
        "touch_discovery_worker(text,text,timestamptz,uuid,text,integer)",
        "is_discovery_worker_healthy(integer)",
        "claim_discovery_run(text,uuid,integer)",
        "heartbeat_discovery_claim(uuid,uuid,text,integer)",
        "transition_discovery_claim(uuid,uuid,text,text,jsonb,jsonb)",
        "finish_discovery_claim(uuid,uuid,text,text,jsonb,jsonb,integer)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION epidemic_discovery.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION epidemic_discovery.{signature} TO epidemic_app")
    op.execute("REVOKE UPDATE ON epidemic_discovery.discovery_runs FROM epidemic_app")
    op.execute("REVOKE INSERT ON epidemic_discovery.discovery_run_events FROM epidemic_app")
    op.execute("REVOKE ALL ON epidemic_discovery.discovery_worker_heartbeats FROM PUBLIC,epidemic_app,epidemicreview")
    op.execute(
        "GRANT SELECT ON epidemic_discovery.review_discovery_runs_v3,epidemic_discovery.review_discovery_events_v3,epidemic_discovery.review_preview_rows_v2 TO epidemicreview"
    )


def downgrade() -> None:
    op.execute("GRANT UPDATE ON epidemic_discovery.discovery_runs TO epidemic_app")
    op.execute("GRANT INSERT ON epidemic_discovery.discovery_run_events TO epidemic_app")
    for view in ("review_preview_rows_v2", "review_discovery_events_v3", "review_discovery_runs_v3"):
        op.execute(f"DROP VIEW epidemic_discovery.{view}")
    for signature in (
        "finish_discovery_claim(uuid,uuid,text,text,jsonb,jsonb,integer)",
        "transition_discovery_claim(uuid,uuid,text,text,jsonb,jsonb)",
        "heartbeat_discovery_claim(uuid,uuid,text,integer)",
        "claim_discovery_run(text,uuid,integer)",
        "touch_discovery_worker(text,text,timestamptz,uuid,text,integer)",
        "is_discovery_worker_healthy(integer)",
    ):
        op.execute(f"DROP FUNCTION epidemic_discovery.{signature}")
    op.execute("DROP TABLE epidemic_discovery.discovery_worker_heartbeats")
    op.execute("ALTER TABLE epidemic_discovery.discovery_run_events DROP COLUMN requires_user_action")
    op.execute("DROP INDEX epidemic_discovery.ix_discovery_runs_claimable")
    op.execute("""ALTER TABLE epidemic_discovery.discovery_runs
      DROP CONSTRAINT ck_discovery_progress_object,DROP CONSTRAINT ck_discovery_claim_complete,
      DROP COLUMN completed_at,DROP COLUMN request_validated_at,DROP COLUMN progress_json,
      DROP COLUMN terminal_reason_code,DROP COLUMN last_error_detail,DROP COLUMN last_error_code,
      DROP COLUMN heartbeat_at,DROP COLUMN lease_expires_at,DROP COLUMN claim_owner,DROP COLUMN claim_token,
      DROP COLUMN next_attempt_at,DROP COLUMN attempt_count""")
    op.execute("ALTER TABLE epidemic_discovery.discovery_runs DROP CONSTRAINT discovery_runs_state_check")
    op.execute("""ALTER TABLE epidemic_discovery.discovery_runs ADD CONSTRAINT discovery_runs_state_check CHECK(state IN (
      'QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','REVIEW_READY','NO_CANDIDATE','FAILED_RETRYABLE','FAILED_TERMINAL'))""")

# ruff: noqa: E501
"""Add key-free discovery states and isolated agent submissions.

Revision ID: 0009_p1_7a_keyfree_discovery
Revises: 0008_p1_7a_discovery_capability
"""

from alembic import op

revision = "0009_p1_7a_keyfree_discovery"
down_revision = "0008_p1_7a_discovery_capability"
branch_labels = None
depends_on = None

BASE_STATES = (
    "REVIEW_READY",
    "NO_CANDIDATE",
    "DUPLICATE_ONLY",
    "POLICY_BLOCKED",
    "SECURITY_BLOCKED",
    "LICENSE_BLOCKED",
    "UNSUPPORTED_FORMAT",
    "INVALID_REQUEST",
    "FAILED_RETRYABLE",
    "FAILED_TERMINAL",
)
KEYFREE_STATES = BASE_STATES + (
    "DISCOVERY_COMPLETE_NO_CANDIDATE",
    "DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE",
    "PROVIDER_DEGRADED",
)
BASE_RETRY_STATES = (
    "NO_CANDIDATE",
    "DUPLICATE_ONLY",
    "POLICY_BLOCKED",
    "SECURITY_BLOCKED",
    "LICENSE_BLOCKED",
    "UNSUPPORTED_FORMAT",
    "INVALID_REQUEST",
    "FAILED_TERMINAL",
)
KEYFREE_RETRY_STATES = BASE_RETRY_STATES + (
    "DISCOVERY_COMPLETE_NO_CANDIDATE",
    "DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE",
    "PROVIDER_DEGRADED",
)


def _finish_function(states: tuple[str, ...]) -> str:
    allowed = ",".join(f"'{item}'" for item in states)
    return f"""CREATE OR REPLACE FUNCTION epidemic_discovery.finish_discovery_claim(
      p_run uuid,p_token uuid,p_state text,p_reason text,p_details jsonb,p_progress jsonb,p_retry_seconds integer DEFAULT 0) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE old_state text; terminal boolean;
      BEGIN
        IF p_state NOT IN ({allowed}) THEN RAISE EXCEPTION 'INVALID_TERMINAL_STATE'; END IF;
        SELECT state INTO old_state FROM epidemic_discovery.discovery_runs WHERE id=p_run AND claim_token=p_token FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        terminal=p_state<>'FAILED_RETRYABLE';
        UPDATE epidemic_discovery.discovery_runs SET state=p_state,state_version=state_version+1,
          terminal_reason_code=CASE WHEN terminal THEN p_reason ELSE NULL END,last_error_code=CASE WHEN p_state LIKE 'FAILED_%' THEN p_reason ELSE NULL END,
          last_error_detail=CASE WHEN p_state LIKE 'FAILED_%' THEN left(COALESCE(p_details->>'message',''),1000) ELSE NULL END,
          progress_json=progress_json||COALESCE(p_progress,'{{}}'),next_attempt_at=CASE WHEN p_state='FAILED_RETRYABLE' THEN now()+make_interval(secs=>greatest(1,p_retry_seconds)) ELSE next_attempt_at END,
          completed_at=CASE WHEN terminal THEN now() ELSE NULL END,updated_at=now(),claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL WHERE id=p_run;
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256,requires_user_action)
          VALUES(gen_random_uuid(),p_run,old_state,p_state,p_reason,COALESCE(p_details,'{{}}'),
          encode(sha256(convert_to(p_run::text||p_token::text||p_reason||clock_timestamp()::text,'UTF8')),'hex'),p_state='INVALID_REQUEST');
        RETURN true;
      END$$"""


def _retry_function(states: tuple[str, ...]) -> str:
    allowed = ",".join(f"'{item}'" for item in states)
    return f"""CREATE OR REPLACE FUNCTION epidemic_discovery.retry_discovery_run(p_source uuid) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE source_run epidemic_discovery.discovery_runs%ROWTYPE; request_value jsonb; digest_sha text; run_id uuid; event_detail jsonb;
      BEGIN
        SELECT * INTO source_run FROM epidemic_discovery.discovery_runs WHERE id=p_source;
        IF NOT FOUND THEN RAISE EXCEPTION 'DISCOVERY_RUN_NOT_FOUND'; END IF;
        IF source_run.state NOT IN ({allowed}) THEN RAISE EXCEPTION 'DISCOVERY_RUN_NOT_RETRYABLE'; END IF;
        request_value=source_run.request_json||jsonb_build_object('retry_of_run_id',p_source,'retry_nonce',gen_random_uuid());
        digest_sha=encode(sha256(convert_to(request_value::text,'UTF8')),'hex');run_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',request_value,digest_sha,'source-discovery-policy-v3',source_run.budget_json);
        event_detail=jsonb_build_object('origin','review_console_linked_retry','retry_of_run_id',p_source);
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
          VALUES(gen_random_uuid(),run_id,NULL,'QUEUED','DISCOVERY_RETRY_QUEUED',event_detail,
          encode(sha256(convert_to(run_id::text||event_detail::text||clock_timestamp()::text,'UTF8')),'hex'));
        PERFORM pg_notify('epidemic_discovery_queue',run_id::text);
        RETURN run_id;
      END$$"""


def upgrade() -> None:
    op.execute("ALTER TABLE epidemic_discovery.discovery_runs DROP CONSTRAINT discovery_runs_state_check")
    op.execute("""ALTER TABLE epidemic_discovery.discovery_runs ADD CONSTRAINT discovery_runs_state_check CHECK(state IN (
      'QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','REVIEW_READY','NO_CANDIDATE',
      'DUPLICATE_ONLY','POLICY_BLOCKED','SECURITY_BLOCKED','LICENSE_BLOCKED','UNSUPPORTED_FORMAT','INVALID_REQUEST',
      'DISCOVERY_COMPLETE_NO_CANDIDATE','DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE','PROVIDER_DEGRADED',
      'FAILED_RETRYABLE','FAILED_TERMINAL'))""")
    op.execute(_finish_function(KEYFREE_STATES))
    op.execute(_retry_function(KEYFREE_RETRY_STATES))
    op.execute("""CREATE TABLE epidemic_discovery.source_agent_submissions(
      id uuid PRIMARY KEY,discovery_run_id uuid NOT NULL REFERENCES epidemic_discovery.discovery_runs(id),
      canonical_url text NOT NULL,canonical_url_sha256 char(64) NOT NULL,domain text NOT NULL,
      provider_id text NOT NULL,title text NOT NULL,publisher text NOT NULL,query_sha256 char(64) NOT NULL,
      status text NOT NULL DEFAULT 'PENDING_DETERMINISTIC_REVIEW',created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(canonical_url_sha256 ~ '^[0-9a-f]{64}$'),CHECK(query_sha256 ~ '^[0-9a-f]{64}$'),
      CHECK(status IN ('PENDING_DETERMINISTIC_REVIEW','DUPLICATE','SAFETY_REJECTED')),
      UNIQUE(discovery_run_id,canonical_url_sha256))""")
    op.execute("""CREATE TRIGGER source_agent_submissions_append_only BEFORE UPDATE OR DELETE
      ON epidemic_discovery.source_agent_submissions FOR EACH ROW EXECUTE FUNCTION epidemic_discovery.reject_mutation()""")
    op.execute("""CREATE FUNCTION epidemic_discovery.submit_agent_source_candidate(
      p_run uuid,p_url text,p_url_sha char(64),p_domain text,p_provider text,p_title text,p_publisher text,p_query_sha char(64)) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE out_id uuid;
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM epidemic_discovery.discovery_runs WHERE id=p_run AND policy_version='source-discovery-policy-v3')
          THEN RAISE EXCEPTION 'RUN_NOT_ELIGIBLE_FOR_AGENT_SUBMISSION'; END IF;
        IF p_url !~ '^https://' OR p_url_sha !~ '^[0-9a-f]{64}$' OR p_query_sha !~ '^[0-9a-f]{64}$' OR
          length(p_url)>2048 OR length(p_domain)>253 OR length(p_provider)>120 OR length(p_title)>500 OR length(p_publisher)>500
          THEN RAISE EXCEPTION 'INVALID_AGENT_SUBMISSION'; END IF;
        SELECT id INTO out_id FROM epidemic_discovery.source_agent_submissions WHERE discovery_run_id=p_run AND canonical_url_sha256=p_url_sha;
        IF out_id IS NOT NULL THEN RETURN out_id; END IF;
        out_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.source_agent_submissions(id,discovery_run_id,canonical_url,canonical_url_sha256,domain,provider_id,title,publisher,query_sha256)
          VALUES(out_id,p_run,p_url,p_url_sha,p_domain,p_provider,left(p_title,500),left(p_publisher,500),p_query_sha);
        RETURN out_id;
      END$$""")
    op.execute("""CREATE FUNCTION epidemic_discovery.submit_keyfree_discovery(p_request jsonb) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE digest_sha text; run_id uuid; event_detail jsonb;
      BEGIN
        IF jsonb_typeof(p_request)<>'object' OR NOT (p_request ?& ARRAY['country_iso3','disease_id','desired_metrics','time_window_kind','preferred_source_tiers','language_preferences','max_candidates'])
          THEN RAISE EXCEPTION 'INVALID_DISCOVERY_REQUEST'; END IF;
        IF NOT EXISTS(SELECT 1 FROM epidemic.countries WHERE alpha3=p_request->>'country_iso3' AND active) OR
          NOT EXISTS(SELECT 1 FROM epidemic.diseases WHERE id=(p_request->>'disease_id')::uuid AND active) THEN RAISE EXCEPTION 'INVALID_CATALOG_SELECTION'; END IF;
        digest_sha=encode(sha256(convert_to(p_request::text,'UTF8')),'hex');
        SELECT id INTO run_id FROM epidemic_discovery.discovery_runs WHERE request_sha256=digest_sha;
        IF run_id IS NOT NULL THEN RETURN run_id; END IF;
        run_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',p_request,digest_sha,'source-discovery-policy-v3',
          jsonb_build_object('queries',5,'results_per_query',20,'deep_candidates',5,'full_downloads',2,
          'http_requests',100,'file_bytes',52428800,'task_bytes',262144000,'global_concurrency',4,
          'domain_concurrency',1,'duration_seconds',900));
        event_detail=jsonb_build_object('origin','query_api_keyfree_v3');
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
          VALUES(gen_random_uuid(),run_id,NULL,'QUEUED','DISCOVERY_QUEUED',event_detail,
          encode(sha256(convert_to(run_id::text||event_detail::text||clock_timestamp()::text,'UTF8')),'hex'));
        PERFORM pg_notify('epidemic_discovery_queue',run_id::text);
        RETURN run_id;
      END$$""")
    for signature in (
        "submit_agent_source_candidate(uuid,text,char,text,text,text,text,char)",
        "submit_keyfree_discovery(jsonb)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION epidemic_discovery.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION epidemic_discovery.{signature} TO epidemic_app")
    op.execute("REVOKE ALL ON epidemic_discovery.source_agent_submissions FROM PUBLIC,epidemic_app,epidemicreview")


def downgrade() -> None:
    op.execute("DROP FUNCTION epidemic_discovery.submit_keyfree_discovery(jsonb)")
    op.execute(
        "DROP FUNCTION epidemic_discovery.submit_agent_source_candidate(uuid,text,char,text,text,text,text,char)"
    )
    op.execute("DROP TABLE epidemic_discovery.source_agent_submissions")
    op.execute(_retry_function(BASE_RETRY_STATES))
    op.execute(_finish_function(BASE_STATES))
    op.execute("ALTER TABLE epidemic_discovery.discovery_runs DROP CONSTRAINT discovery_runs_state_check")
    op.execute(
        "UPDATE epidemic_discovery.discovery_runs SET state='NO_CANDIDATE' "
        "WHERE state='DISCOVERY_COMPLETE_NO_CANDIDATE'"
    )
    op.execute(
        "UPDATE epidemic_discovery.discovery_runs SET state='POLICY_BLOCKED' "
        "WHERE state IN ('DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE','PROVIDER_DEGRADED')"
    )
    op.execute("""ALTER TABLE epidemic_discovery.discovery_runs ADD CONSTRAINT discovery_runs_state_check CHECK(state IN (
      'QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','REVIEW_READY','NO_CANDIDATE',
      'DUPLICATE_ONLY','POLICY_BLOCKED','SECURITY_BLOCKED','LICENSE_BLOCKED','UNSUPPORTED_FORMAT',
      'INVALID_REQUEST','FAILED_RETRYABLE','FAILED_TERMINAL'))""")

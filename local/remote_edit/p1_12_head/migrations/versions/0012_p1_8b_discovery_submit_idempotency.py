# ruff: noqa: E501
"""Serialize identical key-free discovery submissions.

Revision ID: 0012_p1_8b_discovery_submit_idempotency
Revises: 0011_p1_8b_verified_discovery
"""

from alembic import op

revision = "0012_p1_8b_discovery_submit_idempotency"
down_revision = "0011_p1_8b_verified_discovery"
branch_labels = None
depends_on = None


def _function(*, serialized: bool) -> str:
    lock = "PERFORM pg_advisory_xact_lock(hashtextextended(digest_sha,0));" if serialized else ""
    policy = "source-discovery-policy-v4" if serialized else "source-discovery-policy-v3"
    extra_budget = "'artifact_fetches',20," if serialized else ""
    event_detail = (
        "jsonb_build_object('origin','query_api_keyfree_v4','serialized_idempotency',true)"
        if serialized
        else "jsonb_build_object('origin','query_api_keyfree_v3')"
    )
    return f"""CREATE OR REPLACE FUNCTION epidemic_discovery.submit_keyfree_discovery(p_request jsonb) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE digest_sha text; run_id uuid; event_detail jsonb;
      BEGIN
        IF jsonb_typeof(p_request)<>'object' OR NOT (p_request ?& ARRAY['country_iso3','disease_id','desired_metrics','time_window_kind','preferred_source_tiers','language_preferences','max_candidates'])
          THEN RAISE EXCEPTION 'INVALID_DISCOVERY_REQUEST'; END IF;
        IF NOT EXISTS(SELECT 1 FROM epidemic.countries WHERE alpha3=p_request->>'country_iso3' AND active) OR
          NOT EXISTS(SELECT 1 FROM epidemic.diseases WHERE id=(p_request->>'disease_id')::uuid AND active) THEN RAISE EXCEPTION 'INVALID_CATALOG_SELECTION'; END IF;
        digest_sha=encode(sha256(convert_to(p_request::text,'UTF8')),'hex');
        {lock}
        SELECT id INTO run_id FROM epidemic_discovery.discovery_runs WHERE request_sha256=digest_sha;
        IF run_id IS NOT NULL THEN RETURN run_id; END IF;
        run_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',p_request,digest_sha,'{policy}',
          jsonb_build_object('queries',5,'results_per_query',20,'deep_candidates',5,'full_downloads',2,
          {extra_budget}'http_requests',100,'file_bytes',52428800,'task_bytes',262144000,'global_concurrency',4,
          'domain_concurrency',1,'duration_seconds',900));
        event_detail={event_detail};
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
          VALUES(gen_random_uuid(),run_id,NULL,'QUEUED','DISCOVERY_QUEUED',event_detail,
          encode(sha256(convert_to(run_id::text||event_detail::text||clock_timestamp()::text,'UTF8')),'hex'));
        PERFORM pg_notify('epidemic_discovery_queue',run_id::text);
        RETURN run_id;
      END$$"""


def upgrade() -> None:
    op.execute(_function(serialized=True))
    op.execute("REVOKE ALL ON FUNCTION epidemic_discovery.submit_keyfree_discovery(jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION epidemic_discovery.submit_keyfree_discovery(jsonb) TO epidemic_app")


def downgrade() -> None:
    op.execute(_function(serialized=False))
    op.execute("REVOKE ALL ON FUNCTION epidemic_discovery.submit_keyfree_discovery(jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION epidemic_discovery.submit_keyfree_discovery(jsonb) TO epidemic_app")

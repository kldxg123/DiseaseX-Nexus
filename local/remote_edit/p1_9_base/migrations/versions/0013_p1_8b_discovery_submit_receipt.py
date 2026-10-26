# ruff: noqa: E501
"""Return an authoritative created/deduplicated discovery receipt.

Revision ID: 0013_p1_8b_discovery_submit_receipt
Revises: 0012_p1_8b_discovery_submit_idempotency
"""

from alembic import op

revision = "0013_p1_8b_discovery_submit_receipt"
down_revision = "0012_p1_8b_discovery_submit_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE FUNCTION epidemic_discovery.submit_keyfree_discovery_v2(p_request jsonb)
      RETURNS TABLE(run_id uuid,created boolean)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE digest_sha text; event_detail jsonb;
      BEGIN
        IF jsonb_typeof(p_request)<>'object' OR NOT (p_request ?& ARRAY['country_iso3','disease_id','desired_metrics','time_window_kind','preferred_source_tiers','language_preferences','max_candidates'])
          THEN RAISE EXCEPTION 'INVALID_DISCOVERY_REQUEST'; END IF;
        IF NOT EXISTS(SELECT 1 FROM epidemic.countries WHERE alpha3=p_request->>'country_iso3' AND active) OR
          NOT EXISTS(SELECT 1 FROM epidemic.diseases WHERE id=(p_request->>'disease_id')::uuid AND active)
          THEN RAISE EXCEPTION 'INVALID_CATALOG_SELECTION'; END IF;
        digest_sha=encode(sha256(convert_to(p_request::text,'UTF8')),'hex');
        PERFORM pg_advisory_xact_lock(hashtextextended(digest_sha,0));
        SELECT dr.id INTO run_id FROM epidemic_discovery.discovery_runs dr WHERE dr.request_sha256=digest_sha;
        IF run_id IS NOT NULL THEN created=false; RETURN NEXT; RETURN; END IF;
        run_id=gen_random_uuid();created=true;
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',p_request,digest_sha,'source-discovery-policy-v4',
          jsonb_build_object('queries',5,'results_per_query',20,'deep_candidates',5,'full_downloads',2,
          'artifact_fetches',20,'http_requests',100,'file_bytes',52428800,'task_bytes',262144000,'global_concurrency',4,
          'domain_concurrency',1,'duration_seconds',900));
        event_detail=jsonb_build_object('origin','query_api_keyfree_v4','serialized_idempotency',true);
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
          VALUES(gen_random_uuid(),run_id,NULL,'QUEUED','DISCOVERY_QUEUED',event_detail,
          encode(sha256(convert_to(run_id::text||event_detail::text||clock_timestamp()::text,'UTF8')),'hex'));
        PERFORM pg_notify('epidemic_discovery_queue',run_id::text);
        RETURN NEXT;
      END$$""")
    op.execute("REVOKE ALL ON FUNCTION epidemic_discovery.submit_keyfree_discovery_v2(jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION epidemic_discovery.submit_keyfree_discovery_v2(jsonb) TO epidemic_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION epidemic_discovery.submit_keyfree_discovery_v2(jsonb)")

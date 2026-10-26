# ruff: noqa: E501
"""Add controlled linked retry and international source-tier selection.

Revision ID: 0008_p1_7a_discovery_capability
Revises: 0007_p1_7a_discovery_observability
"""

from alembic import op

revision = "0008_p1_7a_discovery_capability"
down_revision = "0007_p1_7a_discovery_observability"
branch_labels = None
depends_on = None


def _submit_function(tiers: tuple[str, ...]) -> str:
    allowed = ",".join(f"'{item}'" for item in tiers)
    return f"""CREATE OR REPLACE FUNCTION epidemic_discovery.submit_structured_discovery(
      p_country_iso3 text,p_disease_id uuid,p_metrics text[],p_time_window text,p_tiers text[],
      p_languages text[],p_max_candidates integer) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE req jsonb; digest_sha text; run_id uuid; event_detail jsonb;
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM epidemic.countries WHERE alpha3=p_country_iso3 AND active) OR
          NOT EXISTS(SELECT 1 FROM epidemic.diseases WHERE id=p_disease_id AND active) THEN RAISE EXCEPTION 'INVALID_CATALOG_SELECTION'; END IF;
        IF p_metrics IS NULL OR cardinality(p_metrics)=0 OR NOT (p_metrics <@ ARRAY['cases_new','deaths_new','incidence_rate']::text[]) OR
          p_time_window NOT IN ('latest_rolling_year','latest_complete_calendar_year','current_year_to_latest_period') OR
          p_tiers IS NULL OR cardinality(p_tiers)=0 OR NOT (p_tiers <@ ARRAY[{allowed}]::text[]) OR
          p_max_candidates NOT BETWEEN 1 AND 20 THEN RAISE EXCEPTION 'INVALID_DISCOVERY_REQUEST'; END IF;
        req=jsonb_build_object('country_iso3',p_country_iso3,'disease_id',p_disease_id,'desired_metrics',p_metrics,
          'time_window_kind',p_time_window,'preferred_source_tiers',p_tiers,'language_preferences',COALESCE(p_languages,ARRAY['en']),
          'max_candidates',p_max_candidates);
        digest_sha=encode(sha256(convert_to(req::text,'UTF8')),'hex');
        SELECT id INTO run_id FROM epidemic_discovery.discovery_runs WHERE request_sha256=digest_sha;
        IF run_id IS NOT NULL THEN RETURN run_id; END IF;
        run_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',req,digest_sha,'source-discovery-policy-v2',
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
      END$$"""


def upgrade() -> None:
    op.execute(
        _submit_function(
            (
                "NATIONAL_HEALTH",
                "GOVERNMENT_OPEN_DATA",
                "REGIONAL_HEALTH",
                "INTERNATIONAL_PUBLIC_HEALTH",
                "PUBLIC_HEALTH_RESEARCH",
            )
        )
    )
    op.execute("""CREATE FUNCTION epidemic_discovery.retry_discovery_run(p_source uuid) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE source_run epidemic_discovery.discovery_runs%ROWTYPE; request_value jsonb; digest_sha text; run_id uuid; event_detail jsonb;
      BEGIN
        SELECT * INTO source_run FROM epidemic_discovery.discovery_runs WHERE id=p_source;
        IF NOT FOUND THEN RAISE EXCEPTION 'DISCOVERY_RUN_NOT_FOUND'; END IF;
        IF source_run.state NOT IN ('NO_CANDIDATE','DUPLICATE_ONLY','POLICY_BLOCKED','SECURITY_BLOCKED','LICENSE_BLOCKED','UNSUPPORTED_FORMAT','INVALID_REQUEST','FAILED_TERMINAL')
          THEN RAISE EXCEPTION 'DISCOVERY_RUN_NOT_RETRYABLE'; END IF;
        request_value=source_run.request_json||jsonb_build_object('retry_of_run_id',p_source,'retry_nonce',gen_random_uuid());
        digest_sha=encode(sha256(convert_to(request_value::text,'UTF8')),'hex');run_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',request_value,digest_sha,'source-discovery-policy-v2',source_run.budget_json);
        event_detail=jsonb_build_object('origin','review_console_linked_retry','retry_of_run_id',p_source);
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
          VALUES(gen_random_uuid(),run_id,NULL,'QUEUED','DISCOVERY_RETRY_QUEUED',event_detail,
          encode(sha256(convert_to(run_id::text||event_detail::text||clock_timestamp()::text,'UTF8')),'hex'));
        PERFORM pg_notify('epidemic_discovery_queue',run_id::text);
        RETURN run_id;
      END$$""")
    op.execute("REVOKE ALL ON FUNCTION epidemic_discovery.retry_discovery_run(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION epidemic_discovery.retry_discovery_run(uuid) TO epidemicreview")


def downgrade() -> None:
    op.execute("DROP FUNCTION epidemic_discovery.retry_discovery_run(uuid)")
    op.execute(
        _submit_function(
            (
                "NATIONAL_HEALTH",
                "GOVERNMENT_OPEN_DATA",
                "REGIONAL_HEALTH",
                "PUBLIC_HEALTH_RESEARCH",
            )
        )
    )

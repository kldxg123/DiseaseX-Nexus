# ruff: noqa: E501
"""Enforce evidence-backed review readiness and register trusted official feeds.

Revision ID: 0022_p1_12_review_ready_trusted_feeds
Revises: 0021_p1_11_ebola_monitoring
"""

from alembic import op

revision = "0022_p1_12_review_ready_trusted_feeds"
down_revision = "0021_p1_11_ebola_monitoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE epidemic_discovery.discovery_runs DROP CONSTRAINT discovery_runs_state_check")
    op.execute(
        """ALTER TABLE epidemic_discovery.discovery_runs ADD CONSTRAINT discovery_runs_state_check CHECK(state IN (
        'QUEUED','CLAIMED','SEARCHING','SEARCH_COMPLETED','SOURCE_FOUND','FETCHING','FETCHED','PARSING','PARSED','VALIDATING','COMPLETED_WITH_REVIEW_READY',
        'DISCOVERING','CANDIDATES_FOUND','PROFILING','REVIEW_READY','NO_CANDIDATE','FETCH_FAILED','PARSE_FAILED','INSUFFICIENT_STRUCTURED_DATA','BLOCKED_BY_SOURCE_POLICY',
        'DUPLICATE_ONLY','POLICY_BLOCKED','SECURITY_BLOCKED','LICENSE_BLOCKED','UNSUPPORTED_FORMAT','INVALID_REQUEST',
        'DISCOVERY_COMPLETE_NO_CANDIDATE','DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE','PROVIDER_DEGRADED','FAILED_RETRYABLE','FAILED_TERMINAL'))"""
    )
    op.execute("CREATE SCHEMA epidemic_p1_12 AUTHORIZATION epidemicdb")
    op.execute("REVOKE ALL ON SCHEMA epidemic_p1_12 FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA epidemic_p1_12 TO epidemic_app")
    op.execute(
        """DO $$BEGIN
        IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemictrusted') THEN
          CREATE ROLE epidemictrusted LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
        END IF;
        END$$"""
    )
    op.execute("ALTER ROLE epidemictrusted SET search_path=pg_catalog")
    op.execute("REVOKE ALL ON SCHEMA epidemic,epidemic_p1_12 FROM epidemictrusted")
    op.execute("GRANT USAGE ON SCHEMA epidemic,epidemic_p1_12 TO epidemictrusted")
    op.execute(
        """CREATE TABLE epidemic_p1_12.discovery_requests_v2(
        id uuid PRIMARY KEY,discovery_run_id uuid NOT NULL UNIQUE REFERENCES epidemic_discovery.discovery_runs(id),
        country_iso3 char(3) NOT NULL,country_name text NOT NULL,disease_id text NOT NULL,
        requested_metrics jsonb NOT NULL,time_window jsonb NOT NULL,source_tiers jsonb NOT NULL,
        request_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(country_iso3 ~ '^[A-Z]{3}$'),CHECK(jsonb_typeof(requested_metrics)='array' AND jsonb_array_length(requested_metrics)>0),
        CHECK(jsonb_typeof(time_window)='object' AND time_window ? 'kind'),
        CHECK(jsonb_typeof(source_tiers)='array' AND jsonb_array_length(source_tiers)>0),
        CHECK(request_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.discovery_queries_v2(
        id uuid PRIMARY KEY,discovery_request_id uuid NOT NULL REFERENCES epidemic_p1_12.discovery_requests_v2(id),
        provider_id text NOT NULL,language_tag text NOT NULL,query_text text NOT NULL,query_sha256 char(64) NOT NULL,
        state text NOT NULL,executed_at timestamptz,result_count integer NOT NULL DEFAULT 0,elapsed_ms integer,
        evidence_sha256 char(64),created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(state IN ('PLANNED','EXECUTED_NO_RESULT','SUCCEEDED','PROVIDER_DEGRADED','FAILED')),
        CHECK((state='PLANNED' AND executed_at IS NULL) OR (state<>'PLANNED' AND executed_at IS NOT NULL)),
        CHECK(result_count>=0),CHECK(query_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
        UNIQUE(discovery_request_id,provider_id,query_sha256))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.candidate_extraction_runs(
        id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL UNIQUE REFERENCES epidemic_discovery.source_candidate_versions(id),
        artifact_id uuid NOT NULL REFERENCES epidemic_discovery.quarantine_artifacts(id),parser_name text NOT NULL,
        extractor_version text NOT NULL,transform_version text NOT NULL,state text NOT NULL,
        started_at timestamptz NOT NULL,finished_at timestamptz NOT NULL,parsed_table_count integer NOT NULL,
        raw_row_count integer NOT NULL,parsed_row_count integer NOT NULL,warning_count integer NOT NULL DEFAULT 0,
        source_locator_policy text NOT NULL,extraction_sha256 char(64) NOT NULL UNIQUE,details jsonb NOT NULL DEFAULT '{}'::jsonb,
        CHECK(state IN ('PARSED','PARSED_WITH_WARNINGS','NEEDS_MAPPING','FAILED')),
        CHECK(finished_at>=started_at AND parsed_table_count>=0 AND raw_row_count>=0 AND parsed_row_count>=0 AND warning_count>=0),
        CHECK(extraction_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.candidate_preview_rows_v2(
        id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
        extraction_run_id uuid NOT NULL REFERENCES epidemic_p1_12.candidate_extraction_runs(id),row_ordinal bigint NOT NULL,
        country_iso3 char(3) NOT NULL,country_name text NOT NULL,disease_id text NOT NULL,pathogen_variant text,
        report_date date,source_as_of_date date,period_start date,period_end date,metric_code text NOT NULL,
        value_numeric numeric NOT NULL,unit text NOT NULL,case_definition text NOT NULL,aggregation_type text NOT NULL,
        geographic_scope text NOT NULL,source_url text NOT NULL,artifact_sha256 char(64) NOT NULL,
        source_row_locator text NOT NULL,raw_value text NOT NULL,normalized_value text NOT NULL,
        extractor_version text NOT NULL,transform_version text NOT NULL,source_fields jsonb NOT NULL,
        mapping_json jsonb NOT NULL,quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,license_status text NOT NULL,
        row_sha256 char(64) NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(country_iso3 ~ '^[A-Z]{3}$'),CHECK(unit IN ('person','specimen','detection')),
        CHECK(aggregation_type IN ('new','cumulative','period_total')),
        CHECK(report_date IS NOT NULL OR source_as_of_date IS NOT NULL OR period_start IS NOT NULL),
        CHECK(period_end IS NULL OR period_start IS NOT NULL),CHECK(period_end IS NULL OR period_start<=period_end),
        CHECK(length(trim(source_row_locator))>0),CHECK(source_url ~ '^https://'),
        CHECK(artifact_sha256 ~ '^[0-9a-f]{64}$'),CHECK(row_sha256 ~ '^[0-9a-f]{64}$'),
        UNIQUE(candidate_version_id,row_ordinal),UNIQUE(candidate_version_id,row_sha256))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.candidate_eligibility_v2(
        candidate_version_id uuid PRIMARY KEY REFERENCES epidemic_discovery.source_candidate_versions(id),
        eligibility_version text NOT NULL DEFAULT 'review-ready-v2',eligible boolean NOT NULL,
        planned_query_count integer NOT NULL,executed_query_count integer NOT NULL,provider_success_count integer NOT NULL,
        raw_search_result_count integer NOT NULL,deduplicated_url_count integer NOT NULL,fetch_attempt_count integer NOT NULL,
        fetch_success_count integer NOT NULL,artifact_count integer NOT NULL,extraction_run_count integer NOT NULL,
        parsed_table_count integer NOT NULL,candidate_preview_row_count integer NOT NULL,mappable_person_metric_count integer NOT NULL,
        artifact_sha256 char(64) NOT NULL,preview_sha256 char(64) NOT NULL,transform_version text NOT NULL,
        eligibility_hash char(64) NOT NULL UNIQUE,reasons jsonb NOT NULL DEFAULT '[]'::jsonb,calculated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(eligibility_version='review-ready-v2'),CHECK(artifact_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(preview_sha256 ~ '^[0-9a-f]{64}$'),CHECK(eligibility_hash ~ '^[0-9a-f]{64}$'),
        CHECK(planned_query_count>=0 AND executed_query_count>=0 AND provider_success_count>=0 AND raw_search_result_count>=0 AND deduplicated_url_count>=0 AND fetch_attempt_count>=0 AND fetch_success_count>=0 AND artifact_count>=0 AND extraction_run_count>=0 AND parsed_table_count>=0 AND candidate_preview_row_count>=0 AND mappable_person_metric_count>=0))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.candidate_state_events(
        id bigserial PRIMARY KEY,candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
        review_case_id uuid REFERENCES epidemic_discovery.source_review_cases(id),from_state text,to_state text NOT NULL,
        event_code text NOT NULL,reasons jsonb NOT NULL DEFAULT '[]'::jsonb,event_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.review_application_bindings(
        review_case_id uuid PRIMARY KEY REFERENCES epidemic_discovery.source_review_cases(id),
        candidate_version_id uuid NOT NULL UNIQUE REFERENCES epidemic_discovery.source_candidate_versions(id),
        artifact_sha256 char(64) NOT NULL,transform_version text NOT NULL,eligibility_hash char(64) NOT NULL,
        bound_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(candidate_version_id) REFERENCES epidemic_p1_12.candidate_eligibility_v2(candidate_version_id),
        UNIQUE(candidate_version_id,artifact_sha256,transform_version,eligibility_hash))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.review_decision_bindings(
        review_decision_id uuid PRIMARY KEY REFERENCES epidemic_discovery.source_review_decisions(id),
        review_case_id uuid NOT NULL REFERENCES epidemic_p1_12.review_application_bindings(review_case_id),
        candidate_version_id uuid NOT NULL,artifact_sha256 char(64) NOT NULL,transform_version text NOT NULL,
        eligibility_hash char(64) NOT NULL,bound_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(candidate_version_id,artifact_sha256,transform_version,eligibility_hash)
          REFERENCES epidemic_p1_12.review_application_bindings(candidate_version_id,artifact_sha256,transform_version,eligibility_hash))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.legacy_reclassification_audit(
        candidate_version_id uuid PRIMARY KEY,review_case_id uuid NOT NULL,old_review_state text NOT NULL,
        old_candidate_status text NOT NULL,new_state text NOT NULL,reasons jsonb NOT NULL,reclassified_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_source_contracts(
        id uuid PRIMARY KEY,source_key text NOT NULL,contract_version text NOT NULL,source_name text NOT NULL,publisher text NOT NULL,
        official_domain text NOT NULL,canonical_url text NOT NULL,terms_url text NOT NULL,disease_id text NOT NULL,
        country_selection_rule text NOT NULL,pathogen_scope text,adapter_version text NOT NULL,transform_version text NOT NULL,
        schema_sha256 char(64) NOT NULL,terms_artifact_sha256 char(64),status text NOT NULL DEFAULT 'TRUSTED_ACTIVE',
        onboarding_mode text NOT NULL,authorization_scope text NOT NULL,trust_tier text NOT NULL,authorization_origin text NOT NULL,
        active boolean NOT NULL DEFAULT true,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(source_key,contract_version),CHECK(status IN ('TRUSTED_ACTIVE','DRIFT_REVIEW_REQUIRED','DISABLED')),
        CHECK(onboarding_mode='CURATED_BUILTIN_SOURCE'),CHECK(authorization_scope='EBOLA_AND_INFLUENZA_ONLY'),
        CHECK(trust_tier='TRUSTED_OFFICIAL_REPORTED'),CHECK(authorization_origin='PROJECT_OWNER_DIRECTIVE'),
        CHECK(schema_sha256 ~ '^[0-9a-f]{64}$'),CHECK(terms_artifact_sha256 IS NULL OR terms_artifact_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_authorization_events(
        id uuid PRIMARY KEY,source_contract_id uuid NOT NULL REFERENCES epidemic_p1_12.trusted_source_contracts(id),
        onboarding_mode text NOT NULL,authorization_scope text NOT NULL,trust_tier text NOT NULL,authorization_origin text NOT NULL,
        directive_sha256 char(64) NOT NULL,event_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_fetch_artifacts(
        id uuid PRIMARY KEY,source_contract_id uuid NOT NULL REFERENCES epidemic_p1_12.trusted_source_contracts(id),
        canonical_url text NOT NULL,http_status integer NOT NULL,mime_type text NOT NULL,byte_size bigint NOT NULL,
        etag text,last_modified text,response_sha256 char(64) NOT NULL,storage_key text NOT NULL,retrieved_at timestamptz NOT NULL,
        elapsed_ms integer NOT NULL,redirect_chain jsonb NOT NULL DEFAULT '[]'::jsonb,source_row_locator_policy text NOT NULL,
        UNIQUE(source_contract_id,canonical_url,response_sha256),CHECK(http_status BETWEEN 200 AND 299),
        CHECK(byte_size>0 AND byte_size<=52428800),CHECK(response_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_observation_lineage(
        observation_id uuid PRIMARY KEY REFERENCES epidemic.normalized_observations(id),
        source_contract_id uuid NOT NULL REFERENCES epidemic_p1_12.trusted_source_contracts(id),
        fetch_artifact_id uuid NOT NULL REFERENCES epidemic_p1_12.trusted_fetch_artifacts(id),
        country_iso3 char(3) NOT NULL,pathogen_variant text,case_definition text NOT NULL,aggregation_type text NOT NULL,
        source_as_of_date date NOT NULL,source_url text NOT NULL,source_row_locator text NOT NULL,
        artifact_sha256 char(64) NOT NULL,extractor_version text NOT NULL,transform_version text NOT NULL,
        source_report_delta boolean NOT NULL DEFAULT false,revision_notice boolean NOT NULL DEFAULT false,
        lineage_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_snapshot_lineage(
        snapshot_id uuid PRIMARY KEY REFERENCES epidemic.dataset_snapshots(id),
        source_contract_id uuid NOT NULL REFERENCES epidemic_p1_12.trusted_source_contracts(id),
        snapshot_sha256 char(64) NOT NULL,row_count bigint NOT NULL,date_min date NOT NULL,date_max date NOT NULL,
        independently_recomputed_sha256 char(64) NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(snapshot_sha256=independently_recomputed_sha256),CHECK(row_count>0 AND date_min<=date_max))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_update_schedules(
        id uuid PRIMARY KEY,schedule_id text NOT NULL UNIQUE,source_contract_id uuid NOT NULL REFERENCES epidemic_p1_12.trusted_source_contracts(id),
        source_contract_version text NOT NULL,timezone text NOT NULL DEFAULT 'Asia/Shanghai',local_time time NOT NULL DEFAULT '02:30',
        enabled boolean NOT NULL DEFAULT true,next_due_at timestamptz NOT NULL,last_checked_at timestamptz,
        last_success_at timestamptz,last_source_change_at timestamptz,source_data_through date,
        consecutive_failures integer NOT NULL DEFAULT 0,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(consecutive_failures>=0))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_update_runs(
        id uuid PRIMARY KEY,schedule_id text NOT NULL REFERENCES epidemic_p1_12.trusted_update_schedules(schedule_id),
        source_contract_version text NOT NULL,scheduled_utc_date date NOT NULL,trigger_type text NOT NULL,state text NOT NULL,
        attempt integer NOT NULL DEFAULT 1,claim_owner text,claim_token uuid,lease_expires_at timestamptz,heartbeat_at timestamptz,
        retry_not_before timestamptz,started_at timestamptz,finished_at timestamptz,source_changed boolean,
        observations_added integer NOT NULL DEFAULT 0,observations_revised integer NOT NULL DEFAULT 0,snapshot_id uuid,
        response_sha256 char(64),etag text,last_modified text,failure_code text,details jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(schedule_id,source_contract_version,scheduled_utc_date),
        CHECK(state IN ('QUEUED','CHECKING','SUCCEEDED_NO_CHANGE','SUCCEEDED_UPDATED','DRIFT_REVIEW_REQUIRED','FAILED_RETRYABLE','FAILED_TERMINAL')),
        CHECK(attempt BETWEEN 1 AND 3 AND observations_added>=0 AND observations_revised>=0))"""
    )
    op.execute(
        """CREATE TABLE epidemic_p1_12.trusted_update_events(
        id bigserial PRIMARY KEY,run_id uuid NOT NULL REFERENCES epidemic_p1_12.trusted_update_runs(id),
        from_state text,to_state text NOT NULL,event_code text NOT NULL,details jsonb NOT NULL DEFAULT '{}'::jsonb,
        event_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute(
        "CREATE FUNCTION epidemic_p1_12.reject_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$BEGIN RAISE EXCEPTION 'P1_12_APPEND_ONLY'; END$$"
    )
    for table in (
        "candidate_extraction_runs",
        "candidate_preview_rows_v2",
        "candidate_state_events",
        "review_application_bindings",
        "review_decision_bindings",
        "trusted_authorization_events",
        "trusted_fetch_artifacts",
        "trusted_observation_lineage",
        "trusted_snapshot_lineage",
        "trusted_update_events",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON epidemic_p1_12.{table} FOR EACH ROW EXECUTE FUNCTION epidemic_p1_12.reject_mutation()"
        )
    op.execute(
        """CREATE FUNCTION epidemic_p1_12.review_ready_v2_violations(p_version uuid) RETURNS text[]
        LANGUAGE plpgsql STABLE SET search_path=pg_catalog,epidemic_p1_12,epidemic_discovery AS $$
        DECLARE v text[]='{}';r record;
        BEGIN
          SELECT dr.id run_id,rq.id request_id,rq.country_iso3,rq.disease_id,rq.time_window,rq.requested_metrics,rq.source_tiers,
            (SELECT count(*) FROM discovery_queries_v2 q WHERE q.discovery_request_id=rq.id) planned,
            (SELECT count(*) FROM discovery_queries_v2 q WHERE q.discovery_request_id=rq.id AND q.state<>'PLANNED') executed,
            (SELECT count(*) FROM discovery_queries_v2 q WHERE q.discovery_request_id=rq.id AND q.state='SUCCEEDED') provider_success,
            (SELECT count(*) FROM epidemic_discovery.source_discovery_search_results s WHERE s.discovery_run_id=dr.id) search_results,
            (SELECT count(DISTINCT s.canonical_url_sha256) FROM epidemic_discovery.source_discovery_search_results s WHERE s.discovery_run_id=dr.id) urls,
            (SELECT count(*) FROM epidemic_discovery.candidate_fetches f WHERE f.discovery_run_id=dr.id) fetches,
            (SELECT count(*) FROM epidemic_discovery.candidate_fetches f WHERE f.discovery_run_id=dr.id AND f.http_status BETWEEN 200 AND 299 AND f.artifact_sha256 IS NOT NULL) fetch_success,
            (SELECT count(*) FROM epidemic_discovery.quarantine_artifacts a WHERE a.candidate_version_id=p_version) artifacts,
            (SELECT count(*) FROM candidate_extraction_runs e WHERE e.candidate_version_id=p_version AND e.state LIKE 'PARSED%') extractions,
            (SELECT coalesce(sum(e.parsed_table_count),0) FROM candidate_extraction_runs e WHERE e.candidate_version_id=p_version AND e.state LIKE 'PARSED%') parsed_tables,
            (SELECT count(*) FROM candidate_preview_rows_v2 p WHERE p.candidate_version_id=p_version) previews,
            (SELECT count(*) FROM candidate_preview_rows_v2 p WHERE p.candidate_version_id=p_version AND p.unit='person' AND p.value_numeric>0) person_values
          INTO r FROM epidemic_discovery.source_candidate_versions cv
          JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id
          JOIN epidemic_discovery.discovery_runs dr ON dr.id=c.discovery_run_id
          LEFT JOIN discovery_requests_v2 rq ON rq.discovery_run_id=dr.id WHERE cv.id=p_version;
          IF r.run_id IS NULL THEN RETURN ARRAY['CANDIDATE_VERSION_NOT_FOUND']; END IF;
          IF r.request_id IS NULL THEN v=array_append(v,'DISCOVERY_REQUEST_V2_REQUIRED'); END IF;
          IF r.country_iso3 IS NULL OR r.country_iso3 !~ '^[A-Z]{3}$' THEN v=array_append(v,'COUNTRY_REQUIRED'); END IF;
          IF coalesce(r.disease_id,'')='' THEN v=array_append(v,'DISEASE_REQUIRED'); END IF;
          IF r.time_window IS NULL OR NOT (r.time_window ? 'kind') THEN v=array_append(v,'TIME_WINDOW_REQUIRED'); END IF;
          IF r.requested_metrics IS NULL OR jsonb_array_length(coalesce(r.requested_metrics,'[]'::jsonb))=0 THEN v=array_append(v,'PERSON_METRIC_REQUEST_REQUIRED'); END IF;
          IF r.source_tiers IS NULL OR jsonb_array_length(coalesce(r.source_tiers,'[]'::jsonb))=0 THEN v=array_append(v,'SOURCE_TIER_REQUIRED'); END IF;
          IF coalesce(r.planned,0)<1 THEN v=array_append(v,'PLANNED_QUERY_REQUIRED'); END IF;
          IF coalesce(r.executed,0)<1 THEN v=array_append(v,'EXECUTED_QUERY_REQUIRED'); END IF;
          IF coalesce(r.provider_success,0)<1 THEN v=array_append(v,'PROVIDER_SUCCESS_REQUIRED'); END IF;
          IF coalesce(r.search_results,0)<1 THEN v=array_append(v,'RAW_SEARCH_RESULT_REQUIRED'); END IF;
          IF coalesce(r.urls,0)<1 THEN v=array_append(v,'DEDUPLICATED_URL_REQUIRED'); END IF;
          IF coalesce(r.fetches,0)<1 THEN v=array_append(v,'FETCH_ATTEMPT_REQUIRED'); END IF;
          IF coalesce(r.fetch_success,0)<1 THEN v=array_append(v,'FETCH_SUCCESS_REQUIRED'); END IF;
          IF coalesce(r.artifacts,0)<1 THEN v=array_append(v,'ARTIFACT_REQUIRED'); END IF;
          IF coalesce(r.extractions,0)<1 THEN v=array_append(v,'EXTRACTION_RUN_REQUIRED'); END IF;
          IF coalesce(r.parsed_tables,0)<1 THEN v=array_append(v,'PARSED_TABLE_REQUIRED'); END IF;
          IF coalesce(r.previews,0)<1 THEN v=array_append(v,'CANDIDATE_PREVIEW_REQUIRED'); END IF;
          IF coalesce(r.person_values,0)<1 THEN v=array_append(v,'POSITIVE_PERSON_METRIC_REQUIRED'); END IF;
          RETURN v;
        END$$"""
    )
    op.execute(
        """CREATE FUNCTION epidemic_p1_12.review_ready_v2_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,epidemic_p1_12 AS $$DECLARE problems text[];e record;
        BEGIN IF NEW.state='REVIEW_READY' AND (TG_OP='INSERT' OR OLD.state IS DISTINCT FROM NEW.state) THEN
          problems=review_ready_v2_violations(NEW.candidate_version_id);
          SELECT * INTO e FROM candidate_eligibility_v2 WHERE candidate_version_id=NEW.candidate_version_id;
          IF cardinality(problems)>0 OR e.candidate_version_id IS NULL OR NOT e.eligible OR jsonb_array_length(e.reasons)>0 THEN
            RAISE EXCEPTION 'REVIEW_READY_V2_REJECTED:%',array_to_string(problems,',') USING ERRCODE='check_violation';
          END IF;
        END IF; RETURN NEW; END$$"""
    )
    op.execute(
        "CREATE TRIGGER review_ready_v2_guard BEFORE INSERT OR UPDATE OF state ON epidemic_discovery.source_review_cases FOR EACH ROW EXECUTE FUNCTION epidemic_p1_12.review_ready_v2_guard()"
    )
    op.execute(
        """CREATE FUNCTION epidemic_p1_12.mark_review_ready_v2(p_version uuid,p_event_sha char(64)) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_p1_12,epidemic_discovery AS $$
        DECLARE e record;c record;problems text[];old_state text;
        BEGIN
          problems=review_ready_v2_violations(p_version);IF cardinality(problems)>0 THEN RAISE EXCEPTION 'REVIEW_READY_V2_REJECTED:%',array_to_string(problems,',');END IF;
          SELECT * INTO e FROM candidate_eligibility_v2 WHERE candidate_version_id=p_version FOR UPDATE;
          IF NOT FOUND OR NOT e.eligible OR jsonb_array_length(e.reasons)>0 THEN RAISE EXCEPTION 'REVIEW_READY_V2_ELIGIBILITY_MISSING';END IF;
          SELECT * INTO c FROM epidemic_discovery.source_review_cases WHERE candidate_version_id=p_version FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'REVIEW_CASE_REQUIRED';END IF;old_state=c.state;
          INSERT INTO review_application_bindings(review_case_id,candidate_version_id,artifact_sha256,transform_version,eligibility_hash)
            VALUES(c.id,p_version,e.artifact_sha256,e.transform_version,e.eligibility_hash) ON CONFLICT(review_case_id) DO NOTHING;
          UPDATE epidemic_discovery.source_review_cases SET state='REVIEW_READY',state_version=state_version+1,updated_at=now(),etag=e.eligibility_hash WHERE id=c.id;
          UPDATE epidemic_discovery.source_candidates sc SET status='REVIEW_READY',state_version=state_version+1 FROM epidemic_discovery.source_candidate_versions cv WHERE cv.id=p_version AND sc.id=cv.candidate_id;
          INSERT INTO candidate_state_events(candidate_version_id,review_case_id,from_state,to_state,event_code,reasons,event_sha256)
            VALUES(p_version,c.id,old_state,'REVIEW_READY','REVIEW_READY_V2_GATE_PASSED','[]'::jsonb,p_event_sha);
          RETURN c.id;
        END$$"""
    )
    op.execute("REVOKE ALL ON FUNCTION epidemic_p1_12.mark_review_ready_v2(uuid,char) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION epidemic_p1_12.mark_review_ready_v2(uuid,char) TO epidemic_app")
    op.execute(
        """CREATE FUNCTION epidemic_p1_12.submit_review_decision_v2(p_case uuid,p_decision text,p_reviewer text,p_reason text,p_key uuid,p_state_version integer) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_p1_12,epidemic_discovery AS $$
        DECLARE b record;c record;out_id uuid;evidence jsonb;decision_sha text;
        BEGIN
          IF p_decision NOT IN ('PREVIEW_APPROVED','CHANGES_REQUESTED','REJECTED') OR length(trim(p_reason))<10 THEN RAISE EXCEPTION 'REVIEW_DECISION_INVALID';END IF;
          SELECT rc.*,rab.candidate_version_id,rab.artifact_sha256,rab.transform_version,rab.eligibility_hash INTO c
          FROM epidemic_discovery.source_review_cases rc JOIN review_application_bindings rab ON rab.review_case_id=rc.id
          WHERE rc.id=p_case FOR UPDATE OF rc;
          IF NOT FOUND OR c.state<>'REVIEW_READY' OR c.state_version<>p_state_version THEN RAISE EXCEPTION 'REVIEW_CASE_STALE';END IF;
          SELECT id INTO out_id FROM epidemic_discovery.source_review_decisions WHERE idempotency_key=p_key;IF out_id IS NOT NULL THEN RETURN out_id;END IF;
          evidence=jsonb_build_object('candidate_version_id',c.candidate_version_id,'artifact_sha256',c.artifact_sha256,'transform_version',c.transform_version,'eligibility_hash',c.eligibility_hash);
          out_id=gen_random_uuid();decision_sha=encode(sha256(convert_to(evidence::text||p_decision||p_reviewer||p_reason,'UTF8')),'hex');
          INSERT INTO epidemic_discovery.source_review_decisions(id,review_case_id,decision,scope,reviewer_id,reason,idempotency_key,evidence_json,evidence_sha256,decision_sha256)
            VALUES(out_id,p_case,p_decision,'CANDIDATE_PREVIEW',p_reviewer,p_reason,p_key,evidence,encode(sha256(convert_to(evidence::text,'UTF8')),'hex'),decision_sha);
          INSERT INTO review_decision_bindings VALUES(out_id,p_case,c.candidate_version_id,c.artifact_sha256,c.transform_version,c.eligibility_hash,now());
          UPDATE epidemic_discovery.source_review_cases SET state=p_decision,state_version=state_version+1,updated_at=now() WHERE id=p_case;
          RETURN out_id;
        END$$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION epidemic_p1_12.submit_review_decision_v2(uuid,text,text,text,uuid,integer) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION epidemic_p1_12.submit_review_decision_v2(uuid,text,text,text,uuid,integer) TO epidemic_app"
    )
    op.execute(
        """CREATE VIEW epidemic_p1_12.review_ready_candidates_v2 AS
        SELECT rc.id review_case_id,rc.state review_state,rc.state_version,rc.etag,cv.id candidate_version_id,cv.version_no,
          sc.id candidate_id,sc.publisher,sc.dataset_name,sc.source_tier,sc.risk_flags,sc.discovery_run_id,
          rq.id discovery_request_id,rq.country_iso3 request_country_iso3,rq.country_name request_country_name,
          rq.disease_id request_disease_id,rq.requested_metrics,rq.time_window,rq.source_tiers,
          cv.canonical_url_redacted,cv.license_status,cv.metadata,e.eligibility_version,e.eligible,
          e.planned_query_count,e.executed_query_count,e.provider_success_count,e.raw_search_result_count,
          e.deduplicated_url_count,e.fetch_attempt_count,e.fetch_success_count,e.artifact_count,e.extraction_run_count,
          e.parsed_table_count,e.candidate_preview_row_count,e.mappable_person_metric_count,e.artifact_sha256,
          e.preview_sha256,e.transform_version,e.eligibility_hash,e.reasons eligibility_reasons,e.calculated_at,
          qa.quality_json,qa.security_json,qa.pii_status,
          a.id artifact_id,a.retrieved_at,a.byte_size artifact_bytes,x.id extraction_run_id,x.parser_name,x.source_locator_policy
        FROM epidemic_discovery.source_review_cases rc JOIN epidemic_p1_12.review_application_bindings b ON b.review_case_id=rc.id
        JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
        JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
        JOIN epidemic_p1_12.discovery_requests_v2 rq ON rq.discovery_run_id=sc.discovery_run_id
        JOIN epidemic_p1_12.candidate_eligibility_v2 e ON e.candidate_version_id=cv.id AND e.eligible
        JOIN epidemic_discovery.quarantine_artifacts a ON a.candidate_version_id=cv.id AND a.sha256=e.artifact_sha256
        JOIN epidemic_p1_12.candidate_extraction_runs x ON x.candidate_version_id=cv.id
        JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
        WHERE rc.state='REVIEW_READY'"""
    )
    op.execute(
        """CREATE VIEW epidemic_p1_12.review_candidate_preview_rows_v2 AS
        SELECT p.*,r.review_case_id,r.discovery_request_id,r.discovery_run_id,r.publisher,r.dataset_name,r.source_tier,
          r.eligibility_hash,r.preview_sha256,r.artifact_id,r.retrieved_at,r.parser_name
        FROM epidemic_p1_12.candidate_preview_rows_v2 p JOIN epidemic_p1_12.review_ready_candidates_v2 r ON r.candidate_version_id=p.candidate_version_id"""
    )
    op.execute(
        """CREATE VIEW epidemic_p1_12.review_insufficient_candidates_v2 AS
        SELECT a.review_case_id,a.candidate_version_id,a.old_review_state,a.old_candidate_status,a.new_state,a.reasons,
          sc.publisher,sc.dataset_name,sc.source_tier,sc.discovery_run_id,cv.canonical_url_redacted,a.reclassified_at
        FROM epidemic_p1_12.legacy_reclassification_audit a JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=a.candidate_version_id
        JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id"""
    )
    op.execute(
        """CREATE VIEW epidemic_p1_12.trusted_dashboard_series_v1 AS
        SELECT co.id source_contract_id,o.id observation_id,o.country_alpha2,c.alpha3 country_iso3,c.name country_name,o.disease_id,d.slug disease_slug,
          d.canonical_name disease_name,o.metric_code,m.display_name metric_name,m.unit,o.value_numeric,o.period_start,o.period_end,
          o.granularity,o.reporting_date,o.as_of,o.dimensions,o.revision,o.supersedes_observation_id,
          l.pathogen_variant,l.case_definition,l.aggregation_type,l.source_as_of_date,l.source_url,l.source_row_locator,
          l.artifact_sha256,l.source_report_delta,l.revision_notice,co.source_key,co.source_name,co.publisher,co.official_domain,
          co.trust_tier,co.contract_version
        FROM epidemic.normalized_observations o JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
        JOIN epidemic_p1_12.trusted_source_contracts co ON co.id=l.source_contract_id JOIN epidemic.countries c ON c.alpha2=o.country_alpha2
        JOIN epidemic.diseases d ON d.id=o.disease_id JOIN epidemic.metric_definitions m ON m.metric_code=o.metric_code"""
    )
    op.execute(
        """CREATE VIEW epidemic_p1_12.trusted_dashboard_catalog_v1 AS
        SELECT s.source_contract_id,co.source_key,co.source_name,co.publisher,co.official_domain,co.contract_version,co.trust_tier,
          s.snapshot_id,s.snapshot_sha256,s.row_count,s.date_min,s.date_max,max(u.last_checked_at) last_checked_at,
          max(u.last_source_change_at) last_source_change_at,max(u.source_data_through) source_data_through,
          array_agg(DISTINCT v.country_iso3 ORDER BY v.country_iso3) countries,
          array_agg(DISTINCT v.disease_slug ORDER BY v.disease_slug) diseases
        FROM epidemic_p1_12.trusted_snapshot_lineage s JOIN epidemic_p1_12.trusted_source_contracts co ON co.id=s.source_contract_id
        JOIN epidemic_p1_12.trusted_dashboard_series_v1 v ON v.source_contract_id=s.source_contract_id
        LEFT JOIN epidemic_p1_12.trusted_update_schedules u ON u.source_contract_id=s.source_contract_id
        GROUP BY s.source_contract_id,co.source_key,co.source_name,co.publisher,co.official_domain,co.contract_version,co.trust_tier,s.snapshot_id,s.snapshot_sha256,s.row_count,s.date_min,s.date_max"""
    )
    op.execute(
        """CREATE VIEW epidemic_p1_12.trusted_update_status_v1 AS
        SELECT s.schedule_id,c.source_key,c.source_name,c.publisher,c.trust_tier,s.source_contract_version,s.enabled,s.timezone,s.local_time,
          s.next_due_at,s.last_checked_at,s.last_success_at,s.last_source_change_at,s.source_data_through,s.consecutive_failures,
          (SELECT r.state FROM epidemic_p1_12.trusted_update_runs r WHERE r.schedule_id=s.schedule_id ORDER BY r.created_at DESC,r.id LIMIT 1) last_run_state
        FROM epidemic_p1_12.trusted_update_schedules s JOIN epidemic_p1_12.trusted_source_contracts c ON c.id=s.source_contract_id"""
    )
    # Snapshot old mutable states before the one-time eligibility recalculation.
    op.execute(
        """INSERT INTO epidemic_p1_12.legacy_reclassification_audit(candidate_version_id,review_case_id,old_review_state,old_candidate_status,new_state,reasons)
        SELECT cv.id,rc.id,rc.state,sc.status,
          CASE WHEN coalesce(dr.request_json->>'country_iso3',dr.request_json->>'country','')='' OR coalesce(dr.request_json->>'disease_id',dr.request_json->>'disease','')='' OR NOT (dr.request_json ? 'time_window')
            THEN 'INVALID_LEGACY_CANDIDATE'
            WHEN NOT EXISTS(SELECT 1 FROM epidemic_discovery.quarantine_artifacts a WHERE a.candidate_version_id=cv.id) THEN 'NEEDS_FETCH'
            WHEN NOT EXISTS(SELECT 1 FROM epidemic_discovery.source_schema_profiles p WHERE p.candidate_version_id=cv.id) THEN 'NEEDS_EXTRACTION'
            ELSE 'NEEDS_MAPPING' END,
          to_jsonb(ARRAY['REVIEW_READY_V2_EVIDENCE_RECALCULATION_REQUIRED'])
        FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
        JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id JOIN epidemic_discovery.discovery_runs dr ON dr.id=sc.discovery_run_id
        WHERE NOT EXISTS(SELECT 1 FROM epidemic_p1_12.candidate_eligibility_v2 e WHERE e.candidate_version_id=cv.id AND e.eligible)"""
    )
    op.execute(
        """INSERT INTO epidemic_p1_12.candidate_state_events(candidate_version_id,review_case_id,from_state,to_state,event_code,reasons,event_sha256)
        SELECT candidate_version_id,review_case_id,old_review_state,new_state,'HISTORICAL_REVIEW_READY_RECALCULATED',reasons,
          encode(sha256(convert_to(candidate_version_id::text||'|p1-12-legacy-reclassification','UTF8')),'hex')
        FROM epidemic_p1_12.legacy_reclassification_audit"""
    )
    op.execute(
        "UPDATE epidemic_discovery.source_review_cases rc SET state=a.new_state,state_version=rc.state_version+1,updated_at=now() FROM epidemic_p1_12.legacy_reclassification_audit a WHERE a.review_case_id=rc.id"
    )
    op.execute(
        "UPDATE epidemic_discovery.source_candidates sc SET status=a.new_state,state_version=sc.state_version+1 FROM epidemic_discovery.source_candidate_versions cv JOIN epidemic_p1_12.legacy_reclassification_audit a ON a.candidate_version_id=cv.id WHERE sc.id=cv.candidate_id"
    )
    # The old P1-11 source classification was incorrect for WHO AFRO.
    op.execute(
        "UPDATE epidemic_discovery.source_candidates SET source_tier='WHO_REGIONAL' WHERE publisher='WHO Regional Office for Africa' AND source_tier='NATIONAL_HEALTH'"
    )
    # Versioned built-in contracts. Production observations are intentionally not inserted by migration.
    for key, name, publisher, domain, url, terms, disease, rule, pathogen in (
        (
            "trusted-ebola-who-bvd-2026",
            "WHO 2026 Bundibugyo virus disease event series",
            "World Health Organization",
            "www.who.int",
            "https://www.who.int/emergencies/emergency-events/item/2026-e000253",
            "https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions",
            "ebola-disease",
            "COD event series discovered from the WHO event index; preserve BDBV and report-country semantics",
            "BDBV",
        ),
        (
            "trusted-influenza-who-flumart",
            "WHO FluID and FluNet official surveillance outputs",
            "World Health Organization",
            "xmart-api-public.who.int",
            "https://www.who.int/teams/global-influenza-programme/surveillance-and-monitoring/influenza-surveillance-outputs",
            "https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions",
            "influenza",
            "Prefer CHN when FluID has >=8 recent person-weeks; otherwise choose latest complete eligible country deterministically",
            None,
        ),
    ):
        op.execute(
            """INSERT INTO epidemic_p1_12.trusted_source_contracts(id,source_key,contract_version,source_name,publisher,official_domain,canonical_url,terms_url,disease_id,country_selection_rule,pathogen_scope,adapter_version,transform_version,schema_sha256,onboarding_mode,authorization_scope,trust_tier,authorization_origin)
            VALUES(gen_random_uuid(),{key},'1.0.0',{name},{publisher},{domain},{url},{terms},{disease},{rule},{pathogen},'p1-12-trusted-adapter-v1','p1-12-trusted-transform-v1',encode(sha256(convert_to({key}||'|1.0.0','UTF8')),'hex'),'CURATED_BUILTIN_SOURCE','EBOLA_AND_INFLUENZA_ONLY','TRUSTED_OFFICIAL_REPORTED','PROJECT_OWNER_DIRECTIVE')""".format(
                key=repr(key),
                name=repr(name),
                publisher=repr(publisher),
                domain=repr(domain),
                url=repr(url),
                terms=repr(terms),
                disease=repr(disease),
                rule=repr(rule),
                pathogen="NULL" if pathogen is None else repr(pathogen),
            )
        )
    op.execute(
        """INSERT INTO epidemic_p1_12.trusted_authorization_events(id,source_contract_id,onboarding_mode,authorization_scope,trust_tier,authorization_origin,directive_sha256,event_sha256)
        SELECT gen_random_uuid(),id,onboarding_mode,authorization_scope,trust_tier,authorization_origin,
          encode(sha256(convert_to('P1-12_PROJECT_OWNER_DIRECTIVE','UTF8')),'hex'),
          encode(sha256(convert_to(id::text||'|P1-12_PROJECT_OWNER_DIRECTIVE','UTF8')),'hex') FROM epidemic_p1_12.trusted_source_contracts"""
    )
    op.execute(
        """INSERT INTO epidemic_p1_12.trusted_update_schedules(id,schedule_id,source_contract_id,source_contract_version,next_due_at)
        SELECT gen_random_uuid(),CASE source_key WHEN 'trusted-ebola-who-bvd-2026' THEN 'trusted-ebola-official-daily-v1' ELSE 'trusted-influenza-official-daily-v1' END,id,contract_version,
          date_trunc('day',now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' + interval '1 day 2 hours 30 minutes'
        FROM epidemic_p1_12.trusted_source_contracts"""
    )
    for code, display, unit, cumulative, description in (
        (
            "ebola_confirmed_cases_cumulative",
            "Ebola confirmed cases (cumulative)",
            "person",
            True,
            "WHO-reported cumulative laboratory-confirmed Ebola cases.",
        ),
        (
            "ebola_confirmed_deaths_cumulative",
            "Ebola confirmed deaths (cumulative)",
            "person",
            True,
            "WHO-reported cumulative deaths among confirmed Ebola cases.",
        ),
        (
            "ebola_recoveries_cumulative",
            "Ebola recoveries (cumulative)",
            "person",
            True,
            "WHO-reported cumulative Ebola recoveries.",
        ),
        (
            "ebola_probable_cases_cumulative",
            "Ebola probable cases (cumulative)",
            "person",
            True,
            "WHO-reported cumulative probable Ebola cases.",
        ),
        (
            "ebola_probable_deaths_cumulative",
            "Ebola probable deaths (cumulative)",
            "person",
            True,
            "WHO-reported cumulative probable Ebola deaths.",
        ),
        (
            "influenza_ili_cases",
            "Influenza-like illness consultations",
            "person",
            False,
            "FluID syndromic ILI persons; not laboratory-confirmed influenza.",
        ),
        (
            "influenza_sari_cases",
            "Severe acute respiratory infection cases",
            "person",
            False,
            "FluID syndromic SARI persons; not laboratory-confirmed influenza.",
        ),
        (
            "influenza_sari_deaths",
            "SARI deaths",
            "person",
            False,
            "FluID reported deaths among SARI surveillance persons.",
        ),
        (
            "influenza_specimens_processed",
            "Influenza specimens processed",
            "specimen",
            False,
            "FluNet specimens processed.",
        ),
        (
            "influenza_positive_detections",
            "Influenza positive detections",
            "detection",
            False,
            "FluNet positive influenza detections.",
        ),
        ("influenza_a_detections", "Influenza A detections", "detection", False, "FluNet influenza A detections."),
        ("influenza_b_detections", "Influenza B detections", "detection", False, "FluNet influenza B detections."),
        (
            "influenza_a_h1n1pdm09_detections",
            "Influenza A(H1N1)pdm09 detections",
            "detection",
            False,
            "FluNet subtype detections.",
        ),
        (
            "influenza_a_h3n2_detections",
            "Influenza A(H3N2) detections",
            "detection",
            False,
            "FluNet subtype detections.",
        ),
    ):
        op.execute(
            """INSERT INTO epidemic.metric_definitions(metric_code,display_name,unit,description,cumulative,allow_negative,min_value,schema_version,active)
            VALUES({code},{display},{unit},{description},{cumulative},false,0,4,true) ON CONFLICT(metric_code) DO NOTHING""".format(
                code=repr(code),
                display=repr(display),
                unit=repr(unit),
                description=repr(description),
                cumulative="true" if cumulative else "false",
            )
        )
    op.execute(
        "GRANT SELECT,INSERT,UPDATE ON epidemic_p1_12.discovery_requests_v2,epidemic_p1_12.discovery_queries_v2,epidemic_p1_12.candidate_eligibility_v2 TO epidemic_app"
    )
    op.execute(
        "GRANT SELECT,INSERT ON epidemic_p1_12.candidate_extraction_runs,epidemic_p1_12.candidate_preview_rows_v2,epidemic_p1_12.candidate_state_events TO epidemic_app"
    )
    op.execute(
        "GRANT SELECT ON epidemic_p1_12.trusted_source_contracts,epidemic_p1_12.trusted_update_schedules,epidemic_p1_12.trusted_update_runs,epidemic_p1_12.trusted_update_events TO epidemic_app"
    )
    op.execute(
        "GRANT INSERT ON epidemic_p1_12.trusted_update_runs,epidemic_p1_12.trusted_update_events TO epidemic_app"
    )
    op.execute(
        "GRANT UPDATE ON epidemic_p1_12.trusted_update_schedules,epidemic_p1_12.trusted_update_runs TO epidemic_app"
    )
    op.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA epidemic_p1_12 TO epidemic_app")
    op.execute(
        """GRANT SELECT ON epidemic.countries,epidemic.diseases,epidemic.metric_definitions,
        epidemic.data_sources,epidemic.query_requests,epidemic.retrieval_runs,epidemic.raw_artifacts,
        epidemic.source_fetches,epidemic.normalized_observations,epidemic.quality_assessments,
        epidemic.dataset_snapshots,epidemic.dataset_snapshot_observations,epidemic.normalization_runs,
        epidemic.retrieval_window_resolutions TO epidemictrusted"""
    )
    op.execute("GRANT INSERT ON epidemic.data_sources TO epidemictrusted")
    op.execute(
        """GRANT INSERT ON epidemic.query_requests,epidemic.retrieval_runs,epidemic.raw_artifacts,
        epidemic.source_fetches,epidemic.normalized_observations,epidemic.quality_assessments,
        epidemic.dataset_snapshots,epidemic.dataset_snapshot_observations,epidemic.normalization_runs,
        epidemic.retrieval_window_resolutions TO epidemictrusted"""
    )
    op.execute(
        """GRANT SELECT ON epidemic_p1_12.trusted_source_contracts,
        epidemic_p1_12.trusted_fetch_artifacts,epidemic_p1_12.trusted_observation_lineage,
        epidemic_p1_12.trusted_snapshot_lineage TO epidemictrusted"""
    )
    op.execute(
        """GRANT INSERT ON epidemic_p1_12.trusted_fetch_artifacts,
        epidemic_p1_12.trusted_observation_lineage,epidemic_p1_12.trusted_snapshot_lineage
        TO epidemictrusted"""
    )
    op.execute(
        "GRANT UPDATE(terms_artifact_sha256,updated_at) ON epidemic_p1_12.trusted_source_contracts TO epidemictrusted"
    )
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT USAGE ON SCHEMA epidemic_p1_12 TO epidemicreview;
          GRANT SELECT ON epidemic_p1_12.review_ready_candidates_v2,epidemic_p1_12.review_candidate_preview_rows_v2,epidemic_p1_12.review_insufficient_candidates_v2 TO epidemicreview;
          GRANT EXECUTE ON FUNCTION epidemic_p1_12.submit_review_decision_v2(uuid,text,text,text,uuid,integer) TO epidemicreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
          GRANT USAGE ON SCHEMA epidemic_p1_12 TO epidemic_dashboard_ro;
          GRANT SELECT ON epidemic_p1_12.trusted_dashboard_series_v1,epidemic_p1_12.trusted_dashboard_catalog_v1,epidemic_p1_12.trusted_update_status_v1 TO epidemic_dashboard_ro;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS review_ready_v2_guard ON epidemic_discovery.source_review_cases")
    op.execute(
        """UPDATE epidemic_discovery.source_review_cases rc SET state=a.old_review_state,state_version=greatest(1,rc.state_version-1),updated_at=now()
        FROM epidemic_p1_12.legacy_reclassification_audit a WHERE a.review_case_id=rc.id"""
    )
    op.execute(
        """UPDATE epidemic_discovery.source_candidates sc SET status=a.old_candidate_status,state_version=greatest(1,sc.state_version-1)
        FROM epidemic_discovery.source_candidate_versions cv JOIN epidemic_p1_12.legacy_reclassification_audit a ON a.candidate_version_id=cv.id WHERE sc.id=cv.candidate_id"""
    )
    op.execute("DROP SCHEMA epidemic_p1_12 CASCADE")
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemictrusted') THEN
          REVOKE ALL ON epidemic.data_sources,epidemic.query_requests,epidemic.retrieval_runs,
          epidemic.raw_artifacts,epidemic.source_fetches,epidemic.normalized_observations,
          epidemic.quality_assessments,epidemic.dataset_snapshots,epidemic.dataset_snapshot_observations,
          epidemic.normalization_runs,epidemic.retrieval_window_resolutions,epidemic.countries,
          epidemic.diseases,epidemic.metric_definitions FROM epidemictrusted;
          REVOKE USAGE ON SCHEMA epidemic FROM epidemictrusted;
          DROP ROLE epidemictrusted;
        END IF;
        END$$"""
    )
    op.execute("ALTER TABLE epidemic_discovery.discovery_runs DROP CONSTRAINT discovery_runs_state_check")
    op.execute(
        """ALTER TABLE epidemic_discovery.discovery_runs ADD CONSTRAINT discovery_runs_state_check CHECK(state IN (
        'QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','REVIEW_READY','NO_CANDIDATE',
        'DUPLICATE_ONLY','POLICY_BLOCKED','SECURITY_BLOCKED','LICENSE_BLOCKED','UNSUPPORTED_FORMAT','INVALID_REQUEST',
        'DISCOVERY_COMPLETE_NO_CANDIDATE','DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE','PROVIDER_DEGRADED',
        'FAILED_RETRYABLE','FAILED_TERMINAL'))"""
    )

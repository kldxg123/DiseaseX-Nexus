# ruff: noqa: E501
"""Add isolated Ebola source monitoring and human-gated preview.

Revision ID: 0021_p1_11_ebola_monitoring
Revises: 0020_p1_10r3_preview_permissions
"""

from alembic import op

revision = "0021_p1_11_ebola_monitoring"
down_revision = "0020_p1_10r3_preview_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA epidemic_ebola AUTHORIZATION epidemicdb")
    op.execute("REVOKE ALL ON SCHEMA epidemic_ebola FROM PUBLIC")
    op.execute(
        "CREATE FUNCTION epidemic_ebola.reject_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$BEGIN RAISE EXCEPTION 'EBOLA_APPEND_ONLY'; END$$"
    )
    op.execute("""CREATE TABLE epidemic_ebola.pathogen_variants(
      code text PRIMARY KEY,name text NOT NULL,virus_species text NOT NULL,aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
      active boolean NOT NULL DEFAULT true,created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE epidemic_ebola.outbreaks(
      id uuid PRIMARY KEY,outbreak_key text NOT NULL UNIQUE,title text NOT NULL,virus_name text NOT NULL,
      pathogen_variant_code text NOT NULL REFERENCES epidemic_ebola.pathogen_variants(code),
      disease_slug text NOT NULL DEFAULT 'ebola-disease',started_on date,status text NOT NULL,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(status IN ('ACTIVE','COUNTDOWN','CLOSED','MONITORING')))""")
    op.execute("""CREATE TABLE epidemic_ebola.outbreak_countries(
      outbreak_id uuid NOT NULL REFERENCES epidemic_ebola.outbreaks(id),country_iso3 char(3) NOT NULL,
      role text NOT NULL,local_transmission boolean,notes text,PRIMARY KEY(outbreak_id,country_iso3),
      CHECK(role IN ('PRIMARY','IMPORTED','CROSS_BORDER','MONITORED')))""")
    op.execute("""CREATE TABLE epidemic_ebola.source_contracts(
      id uuid PRIMARY KEY,outbreak_id uuid NOT NULL REFERENCES epidemic_ebola.outbreaks(id),source_key text NOT NULL UNIQUE,
      source_name text NOT NULL,publisher text NOT NULL,official_domain text NOT NULL,source_family text NOT NULL,
      canonical_url text NOT NULL,adapter_version text NOT NULL,parser_version text NOT NULL,
      license_status text NOT NULL DEFAULT 'REVIEW_REQUIRED',production_usable boolean NOT NULL DEFAULT false,
      training_eligible boolean NOT NULL DEFAULT false,cross_source_sum_allowed boolean NOT NULL DEFAULT false,
      terms_url text,terms_artifact_sha256 char(64),attribution_template text NOT NULL,
      allowed_use text NOT NULL DEFAULT 'INTERNAL_REVIEW_ONLY',redistribution_status text NOT NULL DEFAULT 'REVIEW_REQUIRED',
      automation_status text NOT NULL DEFAULT 'ROBOTS_AND_TERMS_REVIEW_REQUIRED',terms_retrieved_at timestamptz,
      active boolean NOT NULL DEFAULT true,security_policy jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(NOT production_usable AND NOT training_eligible AND NOT cross_source_sum_allowed),
      CHECK(terms_artifact_sha256 IS NULL OR terms_artifact_sha256 ~ '^[0-9a-f]{64}$'))""")
    op.execute("""CREATE TABLE epidemic_ebola.monitor_schedules(
      id uuid PRIMARY KEY,source_id uuid NOT NULL UNIQUE REFERENCES epidemic_ebola.source_contracts(id),
      timezone text NOT NULL DEFAULT 'Asia/Shanghai',local_time time NOT NULL DEFAULT '02:30',active boolean NOT NULL DEFAULT true,
      next_due_at timestamptz NOT NULL,last_checked_at timestamptz,last_success_at timestamptz,last_change_at timestamptz,
      consecutive_failures integer NOT NULL DEFAULT 0,state_version integer NOT NULL DEFAULT 1,updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK(consecutive_failures>=0 AND state_version>=1))""")
    op.execute("""CREATE TABLE epidemic_ebola.monitor_runs(
      id uuid PRIMARY KEY,schedule_id uuid NOT NULL REFERENCES epidemic_ebola.monitor_schedules(id),source_id uuid NOT NULL REFERENCES epidemic_ebola.source_contracts(id),
      state text NOT NULL,attempt integer NOT NULL DEFAULT 1,started_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,
      source_changed boolean,report_count integer NOT NULL DEFAULT 0,observation_count integer NOT NULL DEFAULT 0,
      failure_code text,evidence_sha256 char(64),details jsonb NOT NULL DEFAULT '{}'::jsonb,
      CHECK(state IN ('QUEUED','FETCHING','PARSING','PERSISTING','SUCCEEDED_CHANGED','SUCCEEDED_NO_CHANGE','FAILED_RETRYABLE','FAILED_TERMINAL')),
      CHECK(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'))""")
    op.execute("""CREATE TABLE epidemic_ebola.monitor_events(
      id bigserial PRIMARY KEY,run_id uuid NOT NULL REFERENCES epidemic_ebola.monitor_runs(id),from_state text,to_state text NOT NULL,
      event_code text NOT NULL,details jsonb NOT NULL DEFAULT '{}'::jsonb,event_sha256 char(64) NOT NULL UNIQUE,
      created_at timestamptz NOT NULL DEFAULT now(),CHECK(event_sha256 ~ '^[0-9a-f]{64}$'))""")
    op.execute("""CREATE TABLE epidemic_ebola.source_report_versions(
      id uuid PRIMARY KEY,source_id uuid NOT NULL REFERENCES epidemic_ebola.source_contracts(id),publication_key text NOT NULL,
      publication_date date,source_as_of date NOT NULL,canonical_url_redacted text NOT NULL,mime_type text NOT NULL,
      artifact_sha256 char(64) NOT NULL,artifact_storage_key text NOT NULL,byte_size bigint NOT NULL,
      adapter_version text NOT NULL,parser_version text NOT NULL,content_sha256 char(64) NOT NULL,
      etag text,last_modified text,content_length bigint,redirect_chain_sha256 char(64),fetched_at timestamptz NOT NULL DEFAULT now(),
      extraction_status text NOT NULL,quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,is_current boolean NOT NULL DEFAULT true,
      supersedes_id uuid REFERENCES epidemic_ebola.source_report_versions(id),created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(source_id,publication_key,content_sha256),CHECK(artifact_sha256 ~ '^[0-9a-f]{64}$'),
      CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),CHECK(byte_size BETWEEN 1 AND 52428800),
      CHECK(content_length IS NULL OR content_length>=0),CHECK(redirect_chain_sha256 IS NULL OR redirect_chain_sha256 ~ '^[0-9a-f]{64}$'),
      CHECK(extraction_status IN ('PARSED','PARSED_WITH_WARNINGS','QUARANTINED','UNSUPPORTED')))""")
    op.execute("""CREATE TABLE epidemic_ebola.source_report_observations(
      id uuid PRIMARY KEY,report_version_id uuid NOT NULL REFERENCES epidemic_ebola.source_report_versions(id),
      outbreak_event_id uuid NOT NULL REFERENCES epidemic_ebola.outbreaks(id),pathogen_variant_code text NOT NULL REFERENCES epidemic_ebola.pathogen_variants(code),
      country_iso3 char(3) NOT NULL,reporting_country char(3) NOT NULL,exposure_country char(3),diagnosis_country char(3),treatment_country char(3),
      source_as_of date NOT NULL,metric_code text NOT NULL,value_numeric numeric,
      unit text NOT NULL DEFAULT 'count',observation_semantics text NOT NULL,case_classification text NOT NULL,
      import_classification text NOT NULL,local_transmission boolean,geography_scope text NOT NULL,
      quality_status text NOT NULL,quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,source_row_set_sha256 char(64) NOT NULL,
      revision_number integer NOT NULL DEFAULT 1,provisional boolean NOT NULL DEFAULT true,lineage_sha256 char(64) NOT NULL UNIQUE,
      created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(report_version_id,country_iso3,metric_code,case_classification,import_classification),
      CHECK(metric_code IN ('ebola_confirmed_cases_cumulative','ebola_probable_cases_cumulative','ebola_confirmed_deaths_cumulative','ebola_probable_deaths_cumulative','ebola_recoveries_cumulative','ebola_contacts_listed_cumulative','ebola_contacts_completed_cumulative','ebola_contacts_active','ebola_tests_cumulative','ebola_current_admissions','ebola_alerts_cumulative','ebola_alerts_verified_cumulative','ebola_travellers_screened_cumulative','ebola_travellers_inbound_cumulative','ebola_travellers_outbound_cumulative')),
      CHECK(observation_semantics IN ('source_as_of_cumulative','publication_snapshot_cumulative')),
      CHECK(case_classification IN ('confirmed','probable','not_applicable')),
      CHECK(import_classification IN ('all','imported','local','not_reported')),
      CHECK(quality_status IN ('review_required','source_reported','withheld_low_confidence')),
      CHECK(source_row_set_sha256 ~ '^[0-9a-f]{64}$'),CHECK(revision_number>=1),
      CHECK(lineage_sha256 ~ '^[0-9a-f]{64}$'))""")
    op.execute("""CREATE TABLE epidemic_ebola.source_report_revisions(
      id uuid PRIMARY KEY,source_id uuid NOT NULL REFERENCES epidemic_ebola.source_contracts(id),logical_key text NOT NULL,
      previous_observation_id uuid NOT NULL REFERENCES epidemic_ebola.source_report_observations(id),
      revised_observation_id uuid NOT NULL REFERENCES epidemic_ebola.source_report_observations(id),
      previous_value numeric,revised_value numeric,revision_kind text NOT NULL,detected_at timestamptz NOT NULL DEFAULT now(),
      evidence_sha256 char(64) NOT NULL UNIQUE,CHECK(revision_kind IN ('RETROSPECTIVE_REVISION','CUMULATIVE_REVISION','CLASSIFICATION_CHANGE','SOURCE_CORRECTION')))""")
    op.execute("""CREATE TABLE epidemic_ebola.candidate_bindings(
      candidate_version_id uuid PRIMARY KEY REFERENCES epidemic_discovery.source_candidate_versions(id),
      source_id uuid NOT NULL REFERENCES epidemic_ebola.source_contracts(id),latest_report_version_id uuid NOT NULL REFERENCES epidemic_ebola.source_report_versions(id),
      series_artifact_sha256 char(64) NOT NULL,mapping_contract_sha256 char(64) NOT NULL,
      coverage_status text NOT NULL,pii_status text NOT NULL,cross_source_sum_allowed boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now(),CHECK(NOT cross_source_sum_allowed))""")
    op.execute("""CREATE TABLE epidemic_ebola.preview_decisions(
      id uuid PRIMARY KEY,review_case_id uuid NOT NULL REFERENCES epidemic_discovery.source_review_cases(id),
      candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
      artifact_sha256 char(64) NOT NULL,mapping_sha256 char(64) NOT NULL,reviewer_id text NOT NULL,reason text NOT NULL,
      environment text NOT NULL,test_only boolean NOT NULL,confirmations jsonb NOT NULL,idempotency_key uuid NOT NULL UNIQUE,
      created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(review_case_id,candidate_version_id,environment),
      CHECK(environment IN ('production','p1_11_test')),CHECK((environment='production' AND NOT test_only) OR (environment='p1_11_test' AND test_only)),CHECK(length(trim(reason))>=10))""")
    op.execute("""CREATE TABLE epidemic_ebola.preview_jobs(
      id uuid PRIMARY KEY,decision_id uuid NOT NULL UNIQUE REFERENCES epidemic_ebola.preview_decisions(id),
      state text NOT NULL DEFAULT 'PREVIEW_QUEUED',attempt_count integer NOT NULL DEFAULT 0,max_attempts integer NOT NULL DEFAULT 3,
      claim_owner text,claim_token uuid,lease_expires_at timestamptz,next_attempt_at timestamptz,failure_code text,
      observation_count integer,snapshot_id uuid,publication_id uuid,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK(state IN ('PREVIEW_QUEUED','PREVIEW_CLAIMED','PREVIEW_VALIDATING','PREVIEW_PUBLISHING','PREVIEW_PUBLISHED','FAILED_RETRYABLE','FAILED_FINAL')))""")
    op.execute("""CREATE TABLE epidemic_ebola.preview_observations(
      id uuid PRIMARY KEY,job_id uuid NOT NULL REFERENCES epidemic_ebola.preview_jobs(id),source_observation_id uuid NOT NULL REFERENCES epidemic_ebola.source_report_observations(id),
      source_key text NOT NULL,country_iso3 char(3) NOT NULL,source_as_of date NOT NULL,metric_code text NOT NULL,value_numeric numeric,
      unit text NOT NULL,quality_status text NOT NULL,lineage_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(job_id,source_observation_id))""")
    op.execute("""CREATE TABLE epidemic_ebola.preview_snapshots(
      id uuid PRIMARY KEY,job_id uuid NOT NULL UNIQUE REFERENCES epidemic_ebola.preview_jobs(id),candidate_version_id uuid NOT NULL,
      source_key text NOT NULL,observation_count integer NOT NULL,date_min date NOT NULL,date_max date NOT NULL,
      snapshot_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now(),CHECK(observation_count>0 AND date_min<=date_max))""")
    op.execute("""CREATE TABLE epidemic_ebola.preview_publications(
      id uuid PRIMARY KEY,job_id uuid NOT NULL UNIQUE REFERENCES epidemic_ebola.preview_jobs(id),snapshot_id uuid NOT NULL UNIQUE REFERENCES epidemic_ebola.preview_snapshots(id),
      source_key text NOT NULL,source_name text NOT NULL,publisher text NOT NULL,official_domain text NOT NULL,
      data_tier text NOT NULL DEFAULT 'EBOLA_REVIEW_PREVIEW',production_usable boolean NOT NULL DEFAULT false,
      training_eligible boolean NOT NULL DEFAULT false,cross_source_sum_allowed boolean NOT NULL DEFAULT false,
      publication_sha256 char(64) NOT NULL UNIQUE,published_at timestamptz NOT NULL DEFAULT now(),
      CHECK(data_tier='EBOLA_REVIEW_PREVIEW' AND NOT production_usable AND NOT training_eligible AND NOT cross_source_sum_allowed))""")
    for table in (
        "monitor_events",
        "source_report_observations",
        "source_report_revisions",
        "candidate_bindings",
        "preview_decisions",
        "preview_observations",
        "preview_snapshots",
        "preview_publications",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON epidemic_ebola.{table} FOR EACH ROW EXECUTE FUNCTION epidemic_ebola.reject_mutation()"
        )
    op.execute("""CREATE VIEW epidemic_ebola.review_candidates_v1 AS
      SELECT rc.id review_case_id,rc.state review_state,rc.state_version,rc.etag,sc.id candidate_id,cv.id candidate_version_id,
        sc.discovery_run_id,sc.publisher,sc.dataset_name,sc.status candidate_status,cv.canonical_url_redacted,cv.license_status,
        cv.metadata,qa.pii_status,qa.quality_json,sp.row_count,sp.column_count,sp.date_min,sp.date_max,
        ar.sha256 artifact_sha256,sp.schema_sha256,mp.mapping_sha256,co.source_key,b.coverage_status,
        b.mapping_contract_sha256,co.source_name,co.official_domain,co.source_family,co.production_usable,co.training_eligible,
        pd.id preview_decision_id,pj.id preview_job_id,pj.state preview_state
      FROM epidemic_ebola.candidate_bindings b
      JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=b.candidate_version_id
      JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
      JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id
      JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
      JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
      JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
      JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
      JOIN epidemic_ebola.source_contracts co ON co.id=b.source_id
      LEFT JOIN epidemic_ebola.preview_decisions pd ON pd.review_case_id=rc.id AND pd.environment='production'
      LEFT JOIN epidemic_ebola.preview_jobs pj ON pj.decision_id=pd.id""")
    op.execute("""CREATE VIEW epidemic_ebola.active_preview_publications_v1 AS
      SELECT p.id publication_id,p.source_key,p.source_name,p.publisher,p.official_domain,p.data_tier,p.production_usable,
        p.training_eligible,p.cross_source_sum_allowed,p.published_at,s.id snapshot_id,s.snapshot_sha256,s.observation_count,
        s.date_min,s.date_max,c.license_status,c.attribution_template,c.allowed_use,c.redistribution_status,c.automation_status,
        e.id outbreak_event_id,e.title outbreak_title,e.pathogen_variant_code,e.status outbreak_status
      FROM epidemic_ebola.preview_publications p JOIN epidemic_ebola.preview_jobs j ON j.id=p.job_id
      JOIN epidemic_ebola.preview_snapshots s ON s.id=p.snapshot_id JOIN epidemic_ebola.source_contracts c ON c.source_key=p.source_key
      JOIN epidemic_ebola.outbreaks e ON e.id=c.outbreak_id WHERE j.state='PREVIEW_PUBLISHED'""")
    op.execute("""CREATE VIEW epidemic_ebola.preview_series_v1 AS
      SELECT p.id publication_id,o.id observation_id,o.source_key,o.country_iso3,o.source_as_of,o.metric_code,o.value_numeric,
        o.unit,o.quality_status,o.lineage_sha256,so.reporting_country,so.exposure_country,so.diagnosis_country,so.treatment_country,
        so.case_classification,so.import_classification,so.local_transmission,so.geography_scope,so.quality_flags,
        so.source_row_set_sha256,so.revision_number,so.provisional,v.publication_date,v.fetched_at,v.publication_key,
        v.artifact_sha256,v.canonical_url_redacted FROM epidemic_ebola.preview_publications p
      JOIN epidemic_ebola.preview_observations o ON o.job_id=p.job_id JOIN epidemic_ebola.preview_jobs j ON j.id=p.job_id
      JOIN epidemic_ebola.source_report_observations so ON so.id=o.source_observation_id
      JOIN epidemic_ebola.source_report_versions v ON v.id=so.report_version_id
      WHERE j.state='PREVIEW_PUBLISHED'""")
    op.execute("""CREATE VIEW epidemic_ebola.monitor_status_v1 AS
      SELECT c.source_key,c.source_name,c.publisher,c.official_domain,s.timezone,s.local_time,s.next_due_at,s.last_checked_at,
        s.last_success_at,s.last_change_at,s.consecutive_failures,
        (SELECT r.state FROM epidemic_ebola.monitor_runs r WHERE r.source_id=c.id ORDER BY r.started_at DESC LIMIT 1) last_run_state,
        (SELECT max(v.source_as_of) FROM epidemic_ebola.source_report_versions v WHERE v.source_id=c.id AND v.extraction_status LIKE 'PARSED%') source_data_through
      FROM epidemic_ebola.source_contracts c JOIN epidemic_ebola.monitor_schedules s ON s.source_id=c.id WHERE c.active AND s.active""")
    op.execute("""CREATE FUNCTION epidemic_ebola.submit_preview_decision(p_case uuid,p_reviewer text,p_reason text,p_key uuid,p_state_version integer,p_confirmations jsonb,p_environment text,p_test_only boolean) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_ebola,epidemic_discovery AS $$
      DECLARE c record;out_id uuid;job_id uuid;
      BEGIN
        SELECT rc.id,rc.state,rc.state_version,cv.id candidate_version_id,ar.sha256 artifact_sha256,mp.mapping_sha256
        INTO c FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
        JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
        JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
        JOIN epidemic_ebola.candidate_bindings b ON b.candidate_version_id=cv.id WHERE rc.id=p_case FOR UPDATE OF rc;
        IF NOT FOUND OR c.state<>'REVIEW_READY' OR c.state_version<>p_state_version THEN RAISE EXCEPTION 'EBOLA_PREVIEW_STALE'; END IF;
        IF p_environment NOT IN ('production','p1_11_test') OR (p_environment='production' AND p_test_only) OR (p_environment='p1_11_test' AND NOT p_test_only) THEN RAISE EXCEPTION 'EBOLA_PREVIEW_ENV_INVALID'; END IF;
        IF length(trim(p_reason))<10 THEN RAISE EXCEPTION 'EBOLA_PREVIEW_REASON_REQUIRED'; END IF;
        IF NOT (p_confirmations ?& ARRAY['not_trusted','license_reviewed','aggregate_only','no_cross_source_sum','no_training','no_prediction']) OR EXISTS(SELECT 1 FROM jsonb_each_text(p_confirmations) x WHERE x.value<>'true') THEN RAISE EXCEPTION 'EBOLA_PREVIEW_CONFIRMATIONS_REQUIRED'; END IF;
        SELECT id INTO out_id FROM epidemic_ebola.preview_decisions WHERE idempotency_key=p_key;
        IF out_id IS NOT NULL THEN RETURN out_id; END IF;
        out_id=gen_random_uuid();job_id=gen_random_uuid();
        INSERT INTO epidemic_ebola.preview_decisions(id,review_case_id,candidate_version_id,artifact_sha256,mapping_sha256,reviewer_id,reason,environment,test_only,confirmations,idempotency_key)
        VALUES(out_id,p_case,c.candidate_version_id,c.artifact_sha256,c.mapping_sha256,left(p_reviewer,128),left(trim(p_reason),1000),p_environment,p_test_only,p_confirmations,p_key);
        INSERT INTO epidemic_ebola.preview_jobs(id,decision_id) VALUES(job_id,out_id);
        RETURN out_id;
      END$$""")
    op.execute(
        "REVOKE ALL ON FUNCTION epidemic_ebola.submit_preview_decision(uuid,text,text,uuid,integer,jsonb,text,boolean) FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA epidemic_ebola FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA epidemic_ebola FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA epidemic_ebola FROM PUBLIC")
    op.execute("ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_ebola REVOKE ALL ON TABLES FROM PUBLIC")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_ebola REVOKE ALL ON SEQUENCES FROM PUBLIC"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_ebola REVOKE ALL ON FUNCTIONS FROM PUBLIC"
    )
    op.execute("""DO $$BEGIN
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_app') THEN
        GRANT USAGE ON SCHEMA epidemic_ebola TO epidemic_app;
        GRANT SELECT,INSERT,UPDATE ON epidemic_ebola.pathogen_variants,epidemic_ebola.outbreaks,epidemic_ebola.outbreak_countries,epidemic_ebola.source_contracts,epidemic_ebola.monitor_schedules,epidemic_ebola.monitor_runs,epidemic_ebola.source_report_versions TO epidemic_app;
        GRANT SELECT,INSERT ON epidemic_ebola.source_report_observations,epidemic_ebola.source_report_revisions,epidemic_ebola.candidate_bindings TO epidemic_app;
        GRANT SELECT,INSERT ON epidemic_ebola.monitor_events TO epidemic_app;GRANT USAGE,SELECT ON SEQUENCE epidemic_ebola.monitor_events_id_seq TO epidemic_app;
        REVOKE DELETE ON ALL TABLES IN SCHEMA epidemic_ebola FROM epidemic_app;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
        GRANT USAGE ON SCHEMA epidemic_ebola TO epidemicreview;GRANT SELECT ON epidemic_ebola.review_candidates_v1,epidemic_ebola.monitor_status_v1,epidemic_ebola.preview_jobs TO epidemicreview;
        GRANT EXECUTE ON FUNCTION epidemic_ebola.submit_preview_decision(uuid,text,text,uuid,integer,jsonb,text,boolean) TO epidemicreview;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicpreview') THEN
        GRANT USAGE ON SCHEMA epidemic_ebola TO epidemicpreview;
        GRANT SELECT ON epidemic_ebola.preview_decisions,epidemic_ebola.preview_jobs,epidemic_ebola.candidate_bindings,epidemic_ebola.source_contracts,epidemic_ebola.source_report_versions,epidemic_ebola.source_report_observations TO epidemicpreview;
        GRANT INSERT ON epidemic_ebola.preview_observations,epidemic_ebola.preview_snapshots,epidemic_ebola.preview_publications TO epidemicpreview;
        GRANT UPDATE ON epidemic_ebola.preview_jobs TO epidemicpreview;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
        GRANT USAGE ON SCHEMA epidemic_ebola TO epidemic_dashboard_ro;
        GRANT SELECT ON epidemic_ebola.active_preview_publications_v1,epidemic_ebola.preview_series_v1,epidemic_ebola.monitor_status_v1 TO epidemic_dashboard_ro;
      END IF;
    END$$""")


def downgrade() -> None:
    op.execute("DROP SCHEMA epidemic_ebola CASCADE")

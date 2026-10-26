# ruff: noqa: E501
"""Add the isolated formal candidate preview publication tier.

Revision ID: 0018_p1_10_preview_publication
Revises: 0017_p1_9_crypto_resolution
"""

from alembic import op

revision = "0018_p1_10_preview_publication"
down_revision = "0017_p1_9_crypto_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA epidemic_preview")
    op.execute("REVOKE ALL ON SCHEMA epidemic_preview FROM PUBLIC")
    op.execute(
        """CREATE TABLE epidemic_discovery.discovery_run_candidate_links(
        id uuid PRIMARY KEY,
        discovery_run_id uuid NOT NULL REFERENCES epidemic_discovery.discovery_runs(id),
        candidate_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidates(id),
        candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
        search_result_id uuid REFERENCES epidemic_discovery.source_discovery_search_results(id),
        fetch_id uuid REFERENCES epidemic_discovery.candidate_fetches(id),
        artifact_sha256 char(64) NOT NULL,
        link_reason text NOT NULL,
        linked_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(discovery_run_id,candidate_version_id),
        CHECK(link_reason='REUSED_IDENTICAL_ARTIFACT'),
        CHECK(artifact_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE FUNCTION epidemic_preview.reject_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$BEGIN
          RAISE EXCEPTION 'PREVIEW_IMMUTABLE_OBJECT';
        END$$"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.environment_markers(
        environment text PRIMARY KEY,
        database_name text NOT NULL,
        production boolean NOT NULL,
        disposable boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(environment IN ('production','p1_10_test')),
        CHECK((environment='production' AND production AND NOT disposable) OR
              (environment='p1_10_test' AND NOT production AND disposable)))"""
    )
    op.execute(
        """INSERT INTO epidemic_preview.environment_markers(environment,database_name,production,disposable)
        VALUES('production',current_database(),true,false)"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_review_decisions(
        id uuid PRIMARY KEY,
        review_case_id uuid NOT NULL REFERENCES epidemic_discovery.source_review_cases(id),
        candidate_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidates(id),
        candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
        discovery_run_id uuid NOT NULL REFERENCES epidemic_discovery.discovery_runs(id),
        artifact_sha256 char(64) NOT NULL,
        schema_sha256 char(64) NOT NULL,
        mapping_sha256 char(64) NOT NULL,
        license_evidence_sha256 char(64) NOT NULL,
        binding_sha256 char(64) NOT NULL,
        reviewer_id text NOT NULL,
        reason text NOT NULL,
        scope text NOT NULL DEFAULT 'INTERNAL_REVIEW_ONLY',
        environment text NOT NULL,
        test_only boolean NOT NULL,
        production_usable boolean NOT NULL DEFAULT false,
        training_eligible boolean NOT NULL DEFAULT false,
        confirmations jsonb NOT NULL,
        binding_json jsonb NOT NULL,
        mapping_json jsonb NOT NULL,
        idempotency_key uuid NOT NULL UNIQUE,
        source_state_version integer NOT NULL,
        invalidated_at timestamptz,
        invalidation_reason text,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(review_case_id,candidate_version_id,artifact_sha256,mapping_sha256,environment),
        CHECK(scope='INTERNAL_REVIEW_ONLY'),
        CHECK(NOT production_usable),
        CHECK(NOT training_eligible),
        CHECK(length(trim(reason))>=10),
        CHECK(environment IN ('production','p1_10_test')),
        CHECK((environment='production' AND NOT test_only) OR (environment='p1_10_test' AND test_only)),
        CHECK(artifact_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(schema_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(mapping_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(license_evidence_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(binding_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_publication_jobs(
        id uuid PRIMARY KEY,
        preview_decision_id uuid NOT NULL UNIQUE REFERENCES epidemic_preview.preview_review_decisions(id),
        idempotency_key uuid NOT NULL UNIQUE,
        state text NOT NULL DEFAULT 'PREVIEW_QUEUED',
        state_version integer NOT NULL DEFAULT 1,
        claim_owner text,
        claim_token uuid,
        lease_expires_at timestamptz,
        heartbeat_at timestamptz,
        attempt_count integer NOT NULL DEFAULT 0,
        max_attempts integer NOT NULL DEFAULT 3,
        next_attempt_at timestamptz,
        failure_code text,
        failure_detail_sha256 char(64),
        observation_count integer,
        snapshot_id uuid,
        publication_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(state IN ('PREVIEW_QUEUED','PREVIEW_CLAIMED','PREVIEW_VALIDATING_DECISION',
          'PREVIEW_VALIDATING_ARTIFACT','PREVIEW_VALIDATING_SCOPE','PREVIEW_VALIDATING_MAPPING',
          'PREVIEW_SANITIZING','PREVIEW_AGGREGATING','PREVIEW_QUALITY_CHECKING',
          'PREVIEW_SNAPSHOTTING','PREVIEW_PUBLISHING','PREVIEW_PUBLISHED','PREVIEW_REVOKED',
          'FAILED_RETRYABLE','FAILED_FINAL')),
        CHECK(state_version>=1),
        CHECK(attempt_count>=0 AND max_attempts BETWEEN 1 AND 10),
        CHECK(failure_detail_sha256 IS NULL OR failure_detail_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE INDEX preview_jobs_claim_idx ON epidemic_preview.preview_publication_jobs
        (state,next_attempt_at,lease_expires_at,created_at)"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_publication_events(
        id bigserial PRIMARY KEY,
        publication_job_id uuid NOT NULL REFERENCES epidemic_preview.preview_publication_jobs(id),
        from_state text,
        to_state text NOT NULL,
        event_code text NOT NULL,
        attempt integer NOT NULL DEFAULT 0,
        claim_owner text,
        requires_user_action boolean NOT NULL DEFAULT false,
        error_code text,
        next_step_zh text,
        details jsonb NOT NULL DEFAULT '{}'::jsonb,
        idempotency_key uuid UNIQUE,
        event_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(event_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_mapping_contracts(
        id uuid PRIMARY KEY,
        publication_job_id uuid NOT NULL UNIQUE REFERENCES epidemic_preview.preview_publication_jobs(id),
        candidate_version_id uuid NOT NULL,
        artifact_sha256 char(64) NOT NULL,
        schema_sha256 char(64) NOT NULL,
        source_mapping_sha256 char(64) NOT NULL,
        contract_sha256 char(64) NOT NULL UNIQUE,
        metric_code text NOT NULL,
        display_name_zh text NOT NULL,
        unit text NOT NULL,
        data_nature text NOT NULL,
        training_eligible boolean NOT NULL,
        national_aggregate boolean NOT NULL,
        source_date_semantics text NOT NULL,
        geography_semantics text NOT NULL,
        whitelist jsonb NOT NULL,
        contract_json jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(metric_code='notification_record_count'),
        CHECK(unit='notifications'),
        CHECK(data_nature='derived_aggregation'),
        CHECK(NOT training_eligible),
        CHECK(NOT national_aggregate),
        CHECK(source_date_semantics='notification_date'),
        CHECK(geography_semantics='notification_location'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_observations(
        id uuid PRIMARY KEY,
        publication_job_id uuid NOT NULL REFERENCES epidemic_preview.preview_publication_jobs(id),
        mapping_contract_id uuid NOT NULL REFERENCES epidemic_preview.preview_mapping_contracts(id),
        candidate_version_id uuid NOT NULL,
        artifact_sha256 char(64) NOT NULL,
        observation_date date NOT NULL,
        state_code text NOT NULL,
        municipality_code text NOT NULL,
        metric_code text NOT NULL,
        unit text NOT NULL,
        value_numeric numeric NOT NULL,
        quality_status text NOT NULL,
        source_row_set_sha256 char(64) NOT NULL,
        source_row_count integer NOT NULL,
        record_fingerprint char(64) NOT NULL,
        lineage_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(publication_job_id,record_fingerprint),
        CHECK(metric_code='notification_record_count'),
        CHECK(unit='notifications'),
        CHECK(value_numeric>=0),
        CHECK(source_row_count>0),
        CHECK(source_row_set_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_staging_observations(
        id uuid PRIMARY KEY,
        publication_job_id uuid NOT NULL REFERENCES epidemic_preview.preview_publication_jobs(id),
        row_number integer NOT NULL,
        observation_date date NOT NULL,
        state_code text NOT NULL,
        municipality_code text NOT NULL,
        value_numeric numeric NOT NULL,
        source_row_set_sha256 char(64) NOT NULL,
        source_row_count integer NOT NULL,
        record_fingerprint char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(publication_job_id,row_number),
        UNIQUE(publication_job_id,record_fingerprint),
        CHECK(row_number>0 AND value_numeric>=0 AND source_row_count>0))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_quality_assessments(
        id uuid PRIMARY KEY,
        publication_job_id uuid NOT NULL UNIQUE REFERENCES epidemic_preview.preview_publication_jobs(id),
        status text NOT NULL,
        pii_status text NOT NULL,
        license_status text NOT NULL,
        coverage_status text NOT NULL,
        row_count integer NOT NULL,
        observation_count integer NOT NULL,
        total_value numeric NOT NULL,
        warning_json jsonb NOT NULL,
        assessment_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(status='requires_review'),
        CHECK(row_count>0 AND observation_count>0 AND total_value>=0))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_snapshots(
        id uuid PRIMARY KEY,
        publication_job_id uuid NOT NULL UNIQUE REFERENCES epidemic_preview.preview_publication_jobs(id),
        mapping_contract_id uuid NOT NULL REFERENCES epidemic_preview.preview_mapping_contracts(id),
        candidate_version_id uuid NOT NULL,
        artifact_sha256 char(64) NOT NULL,
        observation_count integer NOT NULL,
        total_value numeric NOT NULL,
        date_min date NOT NULL,
        date_max date NOT NULL,
        state_count integer NOT NULL,
        municipality_count integer NOT NULL,
        snapshot_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(observation_count>0),
        CHECK(total_value>=0),
        CHECK(date_min<=date_max),
        CHECK(state_count>0 AND municipality_count>0))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_snapshot_observations(
        snapshot_id uuid NOT NULL REFERENCES epidemic_preview.preview_snapshots(id),
        observation_id uuid NOT NULL REFERENCES epidemic_preview.preview_observations(id),
        ordinal integer NOT NULL,
        member_sha256 char(64) NOT NULL,
        PRIMARY KEY(snapshot_id,observation_id),
        UNIQUE(snapshot_id,ordinal),
        CHECK(ordinal>0),
        CHECK(member_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_preview.preview_dashboard_publications(
        id uuid PRIMARY KEY,
        publication_job_id uuid NOT NULL UNIQUE REFERENCES epidemic_preview.preview_publication_jobs(id),
        preview_decision_id uuid NOT NULL REFERENCES epidemic_preview.preview_review_decisions(id),
        snapshot_id uuid NOT NULL UNIQUE REFERENCES epidemic_preview.preview_snapshots(id),
        source_key text NOT NULL,
        source_name text NOT NULL,
        publisher text NOT NULL,
        official_domain text NOT NULL,
        country_iso2 char(2) NOT NULL,
        disease_slug text NOT NULL,
        license_status text NOT NULL,
        coverage_status text NOT NULL,
        data_tier text NOT NULL DEFAULT 'REVIEW_PREVIEW',
        production_usable boolean NOT NULL DEFAULT false,
        training_eligible boolean NOT NULL DEFAULT false,
        national_aggregate boolean NOT NULL DEFAULT false,
        publication_sha256 char(64) NOT NULL UNIQUE,
        published_at timestamptz NOT NULL DEFAULT now(),
        CHECK(data_tier='REVIEW_PREVIEW'),
        CHECK(NOT production_usable AND NOT training_eligible AND NOT national_aggregate))"""
    )
    for table in (
        "preview_review_decisions",
        "preview_mapping_contracts",
        "preview_observations",
        "preview_quality_assessments",
        "preview_snapshots",
        "preview_snapshot_observations",
        "preview_dashboard_publications",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON epidemic_preview.{table} "
            "FOR EACH ROW EXECUTE FUNCTION epidemic_preview.reject_mutation()"
        )
    op.execute(
        """CREATE TRIGGER preview_events_append_only BEFORE UPDATE OR DELETE
        ON epidemic_preview.preview_publication_events FOR EACH ROW
        EXECUTE FUNCTION epidemic_preview.reject_mutation()"""
    )
    op.execute(
        """CREATE FUNCTION epidemic_preview.submit_preview_decision(
        p_case uuid,p_reviewer text,p_reason text,p_idempotency uuid,p_state_version integer,
        p_confirmation_text text,p_confirmations jsonb,p_binding jsonb,p_binding_sha char(64),
        p_mapping jsonb,p_mapping_sha char(64),p_environment text,p_test_only boolean) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,epidemic_preview,epidemic_discovery AS $$
        DECLARE c record; out_id uuid;
        BEGIN
          SELECT rc.id review_case_id,rc.state,rc.state_version,cv.id candidate_version_id,
            cv.candidate_id,COALESCE((SELECT l.discovery_run_id FROM epidemic_discovery.discovery_run_candidate_links l
              WHERE l.candidate_version_id=cv.id ORDER BY l.linked_at DESC,l.id DESC LIMIT 1),sc.discovery_run_id) discovery_run_id,
            ar.sha256 artifact_sha256,sp.schema_sha256,
            mp.mapping_sha256,cv.terms_sha256,cv.license_status
          INTO c FROM epidemic_discovery.source_review_cases rc
          JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
          JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
          JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
          JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
          JOIN epidemic_discovery.source_mapping_proposals mp
            ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
          WHERE rc.id=p_case FOR UPDATE OF rc;
          IF NOT FOUND OR c.state<>'REVIEW_READY' OR c.state_version<>p_state_version THEN RAISE EXCEPTION 'PREVIEW_DECISION_STALE'; END IF;
          IF p_environment='p1_9_demo' THEN RAISE EXCEPTION 'P1_9_DEMO_APPROVAL_FORBIDDEN'; END IF;
          IF NOT EXISTS(SELECT 1 FROM epidemic_preview.environment_markers m WHERE m.environment=p_environment AND m.database_name=current_database()) THEN RAISE EXCEPTION 'PREVIEW_ENVIRONMENT_MARKER_MISSING'; END IF;
          IF (p_environment='production' AND p_test_only) OR (p_environment='p1_10_test' AND NOT p_test_only) THEN RAISE EXCEPTION 'PREVIEW_ENVIRONMENT_BOUNDARY_INVALID'; END IF;
          IF p_confirmation_text<>'发布候选预览' THEN RAISE EXCEPTION 'PREVIEW_CONFIRMATION_TEXT_INVALID'; END IF;
          IF length(trim(p_reason))<10 THEN RAISE EXCEPTION 'PREVIEW_REASON_REQUIRED'; END IF;
          IF NOT (p_confirmations ?& ARRAY['not_trusted','license_pending','sample_partial','not_confirmed_cases','not_national','excluded_from_automation','whitelist_only','no_cross_source_sum'])
            OR EXISTS(SELECT 1 FROM jsonb_each_text(p_confirmations) x WHERE x.value<>'true') THEN RAISE EXCEPTION 'PREVIEW_CONFIRMATIONS_INCOMPLETE'; END IF;
          IF p_binding->>'candidate_id'<>c.candidate_id::text OR p_binding->>'candidate_version_id'<>c.candidate_version_id::text
            OR p_binding->>'discovery_run_id'<>c.discovery_run_id::text OR p_binding->>'review_case_id'<>c.review_case_id::text
            OR p_binding->>'artifact_sha256'<>c.artifact_sha256 OR p_binding->>'schema_sha256'<>c.schema_sha256
            OR p_binding->>'mapping_sha256'<>c.mapping_sha256 OR p_binding->>'license_evidence_sha256'<>c.terms_sha256
            THEN RAISE EXCEPTION 'PREVIEW_BINDING_MISMATCH'; END IF;
          IF p_mapping_sha<>c.mapping_sha256 OR p_mapping->>'metric_code'<>'notification_record_count'
            OR p_mapping->>'unit'<>'notifications' OR p_mapping->>'data_nature'<>'derived_aggregation'
            OR COALESCE((p_mapping->>'training_eligible')::boolean,true)
            OR COALESCE((p_mapping->>'national_aggregate')::boolean,true)
            OR p_mapping->>'source_date_semantics'<>'notification_date'
            OR p_mapping->>'geography_semantics'<>'notification_location' THEN RAISE EXCEPTION 'PREVIEW_MAPPING_INVALID'; END IF;
          IF c.license_status ILIKE '%PROHIBIT%' THEN RAISE EXCEPTION 'BLOCKED_PREVIEW_LICENSE_PROHIBITION'; END IF;
          SELECT id INTO out_id FROM epidemic_preview.preview_review_decisions WHERE idempotency_key=p_idempotency;
          IF out_id IS NOT NULL THEN RETURN out_id; END IF;
          out_id=gen_random_uuid();
          INSERT INTO epidemic_preview.preview_review_decisions(
            id,review_case_id,candidate_id,candidate_version_id,discovery_run_id,artifact_sha256,
            schema_sha256,mapping_sha256,license_evidence_sha256,binding_sha256,reviewer_id,reason,
            environment,test_only,confirmations,binding_json,mapping_json,idempotency_key,source_state_version)
          VALUES(out_id,c.review_case_id,c.candidate_id,c.candidate_version_id,c.discovery_run_id,
            c.artifact_sha256,c.schema_sha256,c.mapping_sha256,c.terms_sha256,p_binding_sha,
            left(p_reviewer,128),left(trim(p_reason),1000),p_environment,p_test_only,p_confirmations,
            p_binding,p_mapping,p_idempotency,p_state_version)
          ON CONFLICT(review_case_id,candidate_version_id,artifact_sha256,mapping_sha256,environment) DO NOTHING;
          SELECT id INTO out_id FROM epidemic_preview.preview_review_decisions WHERE review_case_id=c.review_case_id
            AND candidate_version_id=c.candidate_version_id AND artifact_sha256=c.artifact_sha256
            AND mapping_sha256=c.mapping_sha256 AND environment=p_environment;
          RETURN out_id;
        END$$"""
    )
    op.execute(
        """CREATE FUNCTION epidemic_preview.queue_preview_publication(p_decision uuid,p_idempotency uuid) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_preview AS $$
        DECLARE d epidemic_preview.preview_review_decisions%ROWTYPE; out_id uuid; inserted_count integer;
        BEGIN
          SELECT * INTO d FROM epidemic_preview.preview_review_decisions WHERE id=p_decision FOR UPDATE;
          IF NOT FOUND OR d.invalidated_at IS NOT NULL OR d.scope<>'INTERNAL_REVIEW_ONLY'
            OR d.production_usable OR d.training_eligible THEN RAISE EXCEPTION 'VALID_PREVIEW_DECISION_REQUIRED'; END IF;
          SELECT id INTO out_id FROM epidemic_preview.preview_publication_jobs WHERE idempotency_key=p_idempotency OR preview_decision_id=p_decision;
          IF out_id IS NOT NULL THEN RETURN out_id; END IF;
          out_id=gen_random_uuid();
          INSERT INTO epidemic_preview.preview_publication_jobs(id,preview_decision_id,idempotency_key)
          VALUES(out_id,p_decision,p_idempotency) ON CONFLICT DO NOTHING;
          GET DIAGNOSTICS inserted_count=ROW_COUNT;
          SELECT id INTO out_id FROM epidemic_preview.preview_publication_jobs WHERE preview_decision_id=p_decision;
          IF inserted_count=1 THEN
            INSERT INTO epidemic_preview.preview_publication_events(publication_job_id,to_state,event_code,idempotency_key,event_sha256,next_step_zh)
            VALUES(out_id,'PREVIEW_QUEUED','PREVIEW_PUBLICATION_REQUESTED',p_idempotency,
              encode(epidemic_discovery.digest(out_id::text||p_idempotency::text,'sha256'),'hex'),'等待 Preview Worker 领取任务');
          END IF;
          RETURN out_id;
        END$$"""
    )
    op.execute(
        """CREATE FUNCTION epidemic_preview.revoke_preview_publication(p_publication uuid,p_reviewer text,p_reason text,p_idempotency uuid) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_preview AS $$
        DECLARE job uuid; current_state text;
        BEGIN
          SELECT publication_job_id INTO job FROM epidemic_preview.preview_dashboard_publications WHERE id=p_publication;
          IF NOT FOUND THEN RAISE EXCEPTION 'PREVIEW_PUBLICATION_NOT_FOUND'; END IF;
          SELECT to_state INTO current_state FROM epidemic_preview.preview_publication_events WHERE publication_job_id=job ORDER BY id DESC LIMIT 1;
          IF current_state='PREVIEW_REVOKED' THEN RETURN false; END IF;
          IF length(trim(p_reason))<10 THEN RAISE EXCEPTION 'REVOKE_REASON_REQUIRED'; END IF;
          INSERT INTO epidemic_preview.preview_publication_events(publication_job_id,from_state,to_state,event_code,
            requires_user_action,details,idempotency_key,event_sha256,next_step_zh)
          VALUES(job,current_state,'PREVIEW_REVOKED','PREVIEW_REVOKED_BY_REVIEWER',false,
            jsonb_build_object('reviewer',left(p_reviewer,128),'reason',left(trim(p_reason),1000)),p_idempotency,
            encode(epidemic_discovery.digest(job::text||p_idempotency::text||'revoke','sha256'),'hex'),'候选预览已隐藏；历史证据保留')
          ON CONFLICT(idempotency_key) DO NOTHING;
          UPDATE epidemic_preview.preview_publication_jobs SET state='PREVIEW_REVOKED',state_version=state_version+1,updated_at=now() WHERE id=job;
          RETURN true;
        END$$"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.active_preview_publications_v1 AS
        SELECT p.*,s.observation_count,s.total_value,s.date_min,s.date_max,s.state_count,s.municipality_count,
          s.snapshot_sha256,d.artifact_sha256,d.schema_sha256,d.mapping_sha256,d.license_evidence_sha256,
          d.discovery_run_id,d.candidate_id,d.candidate_version_id,d.review_case_id,d.reviewer_id,d.reason,
          mc.contract_sha256,mc.metric_code,mc.display_name_zh,mc.unit,mc.data_nature,
          mc.source_date_semantics,mc.geography_semantics
        FROM epidemic_preview.preview_dashboard_publications p
        JOIN epidemic_preview.preview_snapshots s ON s.id=p.snapshot_id
        JOIN epidemic_preview.preview_review_decisions d ON d.id=p.preview_decision_id
        JOIN epidemic_preview.preview_mapping_contracts mc ON mc.publication_job_id=p.publication_job_id
        WHERE (SELECT e.to_state FROM epidemic_preview.preview_publication_events e
          WHERE e.publication_job_id=p.publication_job_id ORDER BY e.id DESC LIMIT 1)='PREVIEW_PUBLISHED'"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.preview_dashboard_series_v1 AS
        SELECT p.id publication_id,p.source_key,p.source_name,p.publisher,p.official_domain,p.country_iso2,
          p.disease_slug,p.license_status,p.coverage_status,p.data_tier,p.production_usable,
          p.training_eligible,p.national_aggregate,s.id snapshot_id,s.snapshot_sha256,
          o.id observation_id,o.observation_date,o.state_code,o.municipality_code,o.metric_code,
          o.unit,o.value_numeric,o.quality_status,o.source_row_set_sha256,o.source_row_count,
          o.artifact_sha256,o.mapping_contract_id,o.candidate_version_id,o.lineage_sha256
        FROM epidemic_preview.active_preview_publications_v1 p
        JOIN epidemic_preview.preview_snapshots s ON s.id=p.snapshot_id
        JOIN epidemic_preview.preview_snapshot_observations m ON m.snapshot_id=s.id
        JOIN epidemic_preview.preview_observations o ON o.id=m.observation_id"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.preview_lineage_v1 AS
        SELECT p.id publication_id,p.publication_job_id,p.preview_decision_id,p.snapshot_id,
          p.publication_sha256,p.source_key,p.publisher,p.official_domain,p.license_status,p.coverage_status,
          d.discovery_run_id,d.candidate_id,d.candidate_version_id,d.review_case_id,d.artifact_sha256,
          d.schema_sha256,d.mapping_sha256 source_mapping_sha256,d.license_evidence_sha256,d.binding_sha256,
          d.reviewer_id,d.created_at preview_decided_at,mc.id mapping_contract_id,mc.contract_sha256,
          s.snapshot_sha256,s.observation_count,s.total_value,s.created_at snapshot_created_at,
          q.assessment_sha256,q.pii_status,q.status quality_status,j.attempt_count,j.created_at job_created_at,
          p.published_at
        FROM epidemic_preview.preview_dashboard_publications p
        JOIN epidemic_preview.preview_publication_jobs j ON j.id=p.publication_job_id
        JOIN epidemic_preview.preview_review_decisions d ON d.id=p.preview_decision_id
        JOIN epidemic_preview.preview_mapping_contracts mc ON mc.publication_job_id=p.publication_job_id
        JOIN epidemic_preview.preview_snapshots s ON s.id=p.snapshot_id
        JOIN epidemic_preview.preview_quality_assessments q ON q.publication_job_id=p.publication_job_id"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.preview_review_cards_v1 AS
        SELECT rc.id review_case_id,rc.state review_state,rc.state_version,rc.etag,
          cv.id candidate_version_id,cv.candidate_id,cv.license_status,cv.terms_sha256,
          COALESCE((SELECT l.discovery_run_id FROM epidemic_discovery.discovery_run_candidate_links l
            WHERE l.candidate_version_id=cv.id ORDER BY l.linked_at DESC,l.id DESC LIMIT 1),sc.discovery_run_id) discovery_run_id,
          sc.publisher,sc.dataset_name,cv.canonical_url_redacted,
          sp.schema_sha256,sp.row_count,sp.column_count,mp.mapping_sha256,
          ar.sha256 artifact_sha256,ar.byte_size,ar.mime_type,qa.pii_status,
          qa.license_status assessed_license_status,d.id preview_decision_id,d.created_at preview_decided_at,
          j.id preview_job_id,j.state preview_state,j.attempt_count,j.failure_code,j.snapshot_id,j.publication_id
        FROM epidemic_discovery.source_review_cases rc
        JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
        JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
        JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
        JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
        JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
        JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
        LEFT JOIN epidemic_preview.preview_review_decisions d ON d.review_case_id=rc.id AND d.environment='production'
        LEFT JOIN epidemic_preview.preview_publication_jobs j ON j.preview_decision_id=d.id"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW epidemic_discovery.review_discovery_runs_v3 AS
        SELECT dr.id discovery_run_id,dr.state,dr.state_version,dr.request_json,dr.policy_version,dr.budget_json,
          dr.attempt_count,dr.next_attempt_at,dr.claim_owner,dr.lease_expires_at,dr.heartbeat_at,dr.last_error_code,
          dr.last_error_detail,dr.terminal_reason_code,dr.progress_json,dr.created_at,dr.updated_at,dr.completed_at,
          c.alpha2 country_iso2,c.alpha3 country_iso3,c.name country_name_en,
          COALESCE((SELECT ca.alias FROM epidemic.country_aliases ca WHERE ca.country_alpha2=c.alpha2 AND ca.locale LIKE 'zh%' ORDER BY ca.alias_type,ca.id LIMIT 1),c.name) country_name_zh,
          d.id disease_id,d.slug disease_code,d.canonical_name disease_name_en,
          COALESCE((SELECT da.alias FROM epidemic.disease_aliases da WHERE da.disease_id=d.id AND da.language_tag LIKE 'zh%' ORDER BY da.alias_type,da.id LIMIT 1),d.canonical_name) disease_name_zh,
          ((SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id)+
           (SELECT count(*) FROM epidemic_discovery.discovery_run_candidate_links l WHERE l.discovery_run_id=dr.id)) candidate_count,
          ((SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status='REVIEW_READY')+
           (SELECT count(*) FROM epidemic_discovery.discovery_run_candidate_links l JOIN epidemic_discovery.source_candidates sc ON sc.id=l.candidate_id WHERE l.discovery_run_id=dr.id AND sc.status='REVIEW_READY')) qualified_count,
          (SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status<>'REVIEW_READY') blocked_count,
          le.event_code last_event_code,le.created_at last_event_at,le.requires_user_action,
          COALESCE((SELECT bool_or(wh.heartbeat_at>now()-interval '15 seconds') FROM epidemic_discovery.discovery_worker_heartbeats wh),false) worker_healthy,
          extract(epoch FROM (COALESCE(dr.completed_at,now())-dr.created_at))::bigint elapsed_seconds
        FROM epidemic_discovery.discovery_runs dr
        LEFT JOIN epidemic.countries c ON c.alpha3=dr.request_json->>'country_iso3'
        LEFT JOIN epidemic.diseases d ON d.id=CASE
          WHEN dr.request_json->>'disease_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          THEN (dr.request_json->>'disease_id')::uuid ELSE NULL END
        LEFT JOIN LATERAL (SELECT e.event_code,e.created_at,e.requires_user_action FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=dr.id ORDER BY e.created_at DESC,e.id DESC LIMIT 1) le ON true"""
    )
    for signature in (
        "submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,char,jsonb,char,text,boolean)",
        "queue_preview_publication(uuid,uuid)",
        "revoke_preview_publication(uuid,text,text,uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION epidemic_preview.{signature} FROM PUBLIC")
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT USAGE ON SCHEMA epidemic_preview TO epidemicreview;
          GRANT SELECT ON epidemic_discovery.discovery_run_candidate_links TO epidemicreview;
          GRANT SELECT ON epidemic_preview.preview_review_cards_v1,
            epidemic_preview.preview_review_decisions,epidemic_preview.preview_publication_jobs,
            epidemic_preview.preview_publication_events TO epidemicreview;
          GRANT EXECUTE ON FUNCTION epidemic_preview.submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,char,jsonb,char,text,boolean) TO epidemicreview;
          GRANT EXECUTE ON FUNCTION epidemic_preview.queue_preview_publication(uuid,uuid) TO epidemicreview;
          GRANT EXECUTE ON FUNCTION epidemic_preview.revoke_preview_publication(uuid,text,text,uuid) TO epidemicreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
          GRANT USAGE ON SCHEMA epidemic_preview TO epidemic_dashboard_ro;
          GRANT SELECT ON epidemic_preview.active_preview_publications_v1,
            epidemic_preview.preview_dashboard_series_v1,
            epidemic_preview.preview_lineage_v1 TO epidemic_dashboard_ro;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicpreview') THEN
          GRANT USAGE ON SCHEMA epidemic_preview,epidemic_discovery TO epidemicpreview;
          GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA epidemic_preview TO epidemicpreview;
          GRANT DELETE ON epidemic_preview.preview_staging_observations TO epidemicpreview;
          GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA epidemic_preview TO epidemicpreview;
          GRANT SELECT ON epidemic_discovery.source_review_cases,
            epidemic_discovery.source_candidates,epidemic_discovery.source_candidate_versions,
            epidemic_discovery.quarantine_artifacts,epidemic_discovery.source_schema_profiles,
            epidemic_discovery.source_mapping_proposals,epidemic_discovery.source_quality_assessments,
            epidemic_discovery.discovery_runs,epidemic_discovery.source_discovery_search_results,
            epidemic_discovery.candidate_fetches,epidemic_discovery.discovery_run_candidate_links TO epidemicpreview;
          REVOKE ALL ON ALL TABLES IN SCHEMA epidemic FROM epidemicpreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_app') THEN
          GRANT SELECT,INSERT ON epidemic_discovery.discovery_run_candidate_links TO epidemic_app;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA epidemic_preview CASCADE")
    op.execute(
        """CREATE OR REPLACE VIEW epidemic_discovery.review_discovery_runs_v3 AS
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
        LEFT JOIN LATERAL (SELECT e.event_code,e.created_at,e.requires_user_action FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=dr.id ORDER BY e.created_at DESC,e.id DESC LIMIT 1) le ON true"""
    )
    op.execute("DROP TABLE epidemic_discovery.discovery_run_candidate_links")

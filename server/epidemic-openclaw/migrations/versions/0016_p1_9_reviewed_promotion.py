# ruff: noqa: E501
"""Add reviewed, version-bound source promotion and publication.

Revision ID: 0016_p1_9_reviewed_promotion
Revises: 0015_p1_8b_reviewer_read_views
"""

from alembic import op

revision = "0016_p1_9_reviewed_promotion"
down_revision = "0015_p1_8b_reviewer_read_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE epidemic_discovery.source_review_decisions
        ADD COLUMN environment text NOT NULL DEFAULT 'production',
        ADD COLUMN reviewer_kind text NOT NULL DEFAULT 'human_reviewer',
        ADD COLUMN test_only boolean NOT NULL DEFAULT false,
        ADD COLUMN production_usable boolean NOT NULL DEFAULT true,
        ADD COLUMN binding_sha256 char(64),
        ADD COLUMN license_evidence_sha256 char(64),
        ADD COLUMN geographic_scope_decision jsonb NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN metric_decision jsonb NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN invalidated_at timestamptz,
        ADD COLUMN invalidation_reason text,
        ADD CONSTRAINT p1_9_decision_environment CHECK(environment IN ('production','p1_9_demo')),
        ADD CONSTRAINT p1_9_decision_kind CHECK(reviewer_kind IN ('human_reviewer','demo_reviewer','integration_test')),
        ADD CONSTRAINT p1_9_test_decision_boundary CHECK((test_only AND NOT production_usable) OR (NOT test_only)),
        ADD CONSTRAINT p1_9_binding_sha CHECK(binding_sha256 IS NULL OR binding_sha256 ~ '^[0-9a-f]{64}$')"""
    )
    op.execute(
        """ALTER TABLE epidemic_discovery.source_promotion_runs
        ADD COLUMN environment text NOT NULL DEFAULT 'production',
        ADD COLUMN claim_token uuid,
        ADD COLUMN claim_owner text,
        ADD COLUMN lease_expires_at timestamptz,
        ADD COLUMN heartbeat_at timestamptz,
        ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
        ADD COLUMN max_attempts integer NOT NULL DEFAULT 3,
        ADD COLUMN next_attempt_at timestamptz,
        ADD COLUMN last_error_code text,
        ADD COLUMN formal_data_source_id uuid,
        ADD COLUMN normalization_run_id uuid,
        ADD COLUMN publication_id uuid,
        ADD CONSTRAINT p1_9_promotion_environment CHECK(environment IN ('production','p1_9_demo')),
        ADD CONSTRAINT p1_9_promotion_attempts CHECK(attempt_count>=0 AND max_attempts BETWEEN 1 AND 10)"""
    )
    op.execute(
        """CREATE TABLE epidemic_discovery.p1_9_environment_markers(
        environment_type text PRIMARY KEY,
        disposable boolean NOT NULL,
        production_write_forbidden boolean NOT NULL,
        created_for text NOT NULL,
        database_name text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(environment_type='p1_9_demo'),
        CHECK(disposable),
        CHECK(production_write_forbidden),
        CHECK(created_for='P1-9'),
        CHECK(database_name LIKE '%p1_9_demo%'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_discovery.p1_9_mapping_contracts(
        id uuid PRIMARY KEY,
        candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
        mapping_revision integer NOT NULL,
        mapping_sha256 char(64) NOT NULL,
        metric_code text NOT NULL,
        metric_display_name text NOT NULL,
        unit text NOT NULL,
        data_nature text NOT NULL,
        training_eligible boolean NOT NULL,
        national_aggregate boolean NOT NULL,
        source_date_semantics text NOT NULL,
        geography_semantics text NOT NULL,
        contract_json jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(candidate_version_id,mapping_sha256),
        CHECK(mapping_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(data_nature IN ('reported','observed','derived_aggregation','modelled')),
        CHECK(NOT training_eligible OR data_nature IN ('reported','observed')),
        CHECK(metric_code<>'cases_new' OR data_nature IN ('reported','observed')),
        CHECK(metric_code<>'notification_record_count' OR unit='notifications'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_discovery.p1_9_source_versions(
        id uuid PRIMARY KEY,
        data_source_id uuid NOT NULL,
        candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
        adapter_version text NOT NULL,
        mapping_contract_id uuid NOT NULL REFERENCES epidemic_discovery.p1_9_mapping_contracts(id),
        quality_profile_sha256 char(64) NOT NULL,
        artifact_sha256 char(64) NOT NULL,
        license_status text NOT NULL,
        version_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute(
        """CREATE TABLE epidemic_discovery.p1_9_staging_observations(
        id uuid PRIMARY KEY,
        promotion_run_id uuid NOT NULL REFERENCES epidemic_discovery.source_promotion_runs(id),
        row_number bigint NOT NULL,
        period_start date NOT NULL,
        period_end date NOT NULL,
        geography_json jsonb NOT NULL,
        metric_code text NOT NULL,
        value_numeric numeric NOT NULL,
        source_row_set_sha256 char(64) NOT NULL,
        record_fingerprint char(64) NOT NULL,
        quality_status text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(promotion_run_id,row_number),
        UNIQUE(promotion_run_id,record_fingerprint),
        CHECK(period_start<=period_end),
        CHECK(value_numeric>=0),
        CHECK(source_row_set_sha256 ~ '^[0-9a-f]{64}$'),
        CHECK(record_fingerprint ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_discovery.p1_9_publications(
        id uuid PRIMARY KEY,
        promotion_run_id uuid NOT NULL UNIQUE REFERENCES epidemic_discovery.source_promotion_runs(id),
        data_source_id uuid NOT NULL,
        source_version_id uuid NOT NULL REFERENCES epidemic_discovery.p1_9_source_versions(id),
        normalization_run_id uuid NOT NULL,
        snapshot_id uuid NOT NULL,
        dashboard_path text NOT NULL,
        observation_count bigint NOT NULL,
        publication_sha256 char(64) NOT NULL UNIQUE,
        published_at timestamptz NOT NULL DEFAULT now(),
        CHECK(observation_count>0),
        CHECK(publication_sha256 ~ '^[0-9a-f]{64}$'))"""
    )
    op.execute(
        """CREATE TABLE epidemic_discovery.p1_9_observation_lineage(
        observation_id uuid PRIMARY KEY,
        promotion_run_id uuid NOT NULL REFERENCES epidemic_discovery.source_promotion_runs(id),
        mapping_contract_id uuid NOT NULL REFERENCES epidemic_discovery.p1_9_mapping_contracts(id),
        candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
        artifact_sha256 char(64) NOT NULL,
        source_row_set_sha256 char(64) NOT NULL,
        source_row_count integer NOT NULL,
        lineage_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(source_row_count>0))"""
    )
    for table in (
        "p1_9_mapping_contracts",
        "p1_9_source_versions",
        "p1_9_publications",
        "p1_9_observation_lineage",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON epidemic_discovery.{table} "
            "FOR EACH ROW EXECUTE FUNCTION epidemic_discovery.reject_mutation()"
        )
    op.execute(
        """CREATE OR REPLACE FUNCTION epidemic_discovery.p1_9_submit_review_decision(
        p_case uuid,p_action text,p_reviewer text,p_comment text,p_key uuid,p_etag char(64),
        p_environment text,p_reviewer_kind text,p_test_only boolean,p_confirmations jsonb,
        p_geography jsonb,p_metric jsonb,p_binding jsonb,p_binding_sha char(64)) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
        DECLARE c epidemic_discovery.source_review_cases%ROWTYPE; out_id uuid; current_row record;
        BEGIN
          SELECT rc.* INTO c FROM epidemic_discovery.source_review_cases rc WHERE rc.id=p_case FOR UPDATE;
          IF NOT FOUND OR c.state<>'REVIEW_READY' OR c.etag<>p_etag THEN RAISE EXCEPTION 'APPROVAL_STALE'; END IF;
          IF p_action NOT IN ('APPROVED','REJECTED','CHANGES_REQUESTED') THEN RAISE EXCEPTION 'INVALID_DECISION'; END IF;
          IF length(trim(p_comment))<3 THEN RAISE EXCEPTION 'REVIEW_COMMENT_REQUIRED'; END IF;
          IF p_environment NOT IN ('production','p1_9_demo') THEN RAISE EXCEPTION 'ENVIRONMENT_INVALID'; END IF;
          IF p_environment='production' AND (p_test_only OR p_reviewer_kind<>'human_reviewer') THEN RAISE EXCEPTION 'TEST_APPROVAL_PRODUCTION_FORBIDDEN'; END IF;
          IF p_environment='p1_9_demo' AND (NOT p_test_only OR p_reviewer_kind NOT IN ('demo_reviewer','integration_test')) THEN RAISE EXCEPTION 'DEMO_APPROVAL_MARKER_REQUIRED'; END IF;
          IF p_environment='p1_9_demo' AND NOT EXISTS(
            SELECT 1 FROM epidemic_discovery.p1_9_environment_markers m
            WHERE m.environment_type='p1_9_demo' AND m.database_name=current_database()
              AND m.disposable AND m.production_write_forbidden) THEN RAISE EXCEPTION 'DEMO_DATABASE_MARKER_MISSING'; END IF;
          SELECT cv.candidate_id,cv.id candidate_version_id,sc.discovery_run_id,rc.id review_case_id,
            ar.sha256 artifact_sha256,sp.schema_sha256,mp.mapping_sha256,cv.terms_sha256,
            ar.security_report_sha256,cv.license_status,qa.pii_status,sc.risk_flags
          INTO current_row
          FROM epidemic_discovery.source_review_cases rc
          JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
          JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
          JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
          JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
          JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
          JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
          WHERE rc.id=p_case;
          IF p_binding->>'candidate_id'<>current_row.candidate_id::text
            OR p_binding->>'candidate_version_id'<>current_row.candidate_version_id::text
            OR p_binding->>'discovery_run_id'<>current_row.discovery_run_id::text
            OR p_binding->>'review_case_id'<>current_row.review_case_id::text
            OR p_binding->>'artifact_sha256'<>current_row.artifact_sha256
            OR p_binding->>'schema_sha256'<>current_row.schema_sha256
            OR p_binding->>'mapping_sha256'<>current_row.mapping_sha256
            OR p_binding->>'license_evidence_sha256'<>current_row.terms_sha256
            THEN RAISE EXCEPTION 'APPROVAL_BINDING_MISMATCH'; END IF;
          IF p_action='APPROVED' THEN
            IF NOT (p_confirmations ?& ARRAY['license','geography','disease','mapping','metric_unit','time_semantics','second_confirmation'])
              OR NOT COALESCE((p_confirmations->>'license')::boolean,false)
              OR NOT COALESCE((p_confirmations->>'geography')::boolean,false)
              OR NOT COALESCE((p_confirmations->>'disease')::boolean,false)
              OR NOT COALESCE((p_confirmations->>'mapping')::boolean,false)
              OR NOT COALESCE((p_confirmations->>'metric_unit')::boolean,false)
              OR NOT COALESCE((p_confirmations->>'time_semantics')::boolean,false)
              OR NOT COALESCE((p_confirmations->>'second_confirmation')::boolean,false)
              THEN RAISE EXCEPTION 'APPROVAL_CONFIRMATIONS_INCOMPLETE'; END IF;
            IF p_metric->>'metric_code'='cases_new' AND p_metric->>'data_nature' NOT IN ('reported','observed')
              THEN RAISE EXCEPTION 'DERIVED_CASES_MAPPING_FORBIDDEN'; END IF;
            IF p_metric->>'metric_code'='notification_record_count' AND p_metric->>'unit'<>'notifications'
              THEN RAISE EXCEPTION 'NOTIFICATION_UNIT_INVALID'; END IF;
            IF COALESCE(p_geography->>'national_aggregate','true')::boolean THEN RAISE EXCEPTION 'NATIONAL_SCOPE_FORBIDDEN'; END IF;
            IF p_environment='production' AND current_row.license_status LIKE '%REVIEW_REQUIRED%'
              THEN RAISE EXCEPTION 'LICENSE_NOT_VERIFIED'; END IF;
          END IF;
          SELECT id INTO out_id FROM epidemic_discovery.source_review_decisions WHERE idempotency_key=p_key;
          IF out_id IS NOT NULL THEN RETURN out_id; END IF;
          out_id=gen_random_uuid();
          INSERT INTO epidemic_discovery.source_review_decisions(
            id,review_case_id,decision,scope,reviewer_id,reason,idempotency_key,evidence_json,
            evidence_sha256,decision_sha256,environment,reviewer_kind,test_only,production_usable,
            binding_sha256,license_evidence_sha256,geographic_scope_decision,metric_decision)
          VALUES(out_id,p_case,p_action,'VERSION_BOUND',p_reviewer,left(trim(p_comment),500),p_key,p_binding,
            p_binding_sha,encode(digest(p_case::text||p_key::text||p_action||p_binding_sha,'sha256'),'hex'),
            p_environment,p_reviewer_kind,p_test_only,NOT p_test_only,
            p_binding_sha,current_row.terms_sha256,p_geography,p_metric);
          UPDATE epidemic_discovery.source_review_cases
            SET state=p_action,state_version=state_version+1,updated_at=now()
            WHERE id=p_case AND state_version=c.state_version;
          IF NOT FOUND THEN RAISE EXCEPTION 'APPROVAL_STALE'; END IF;
          RETURN out_id;
        END$$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION epidemic_discovery.p1_9_queue_promotion(
        p_decision uuid,p_environment text,p_key char(64)) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
        DECLARE d epidemic_discovery.source_review_decisions%ROWTYPE; out_id uuid; inserted_count integer:=0;
        BEGIN
          SELECT * INTO d FROM epidemic_discovery.source_review_decisions WHERE id=p_decision FOR UPDATE;
          IF NOT FOUND OR d.decision<>'APPROVED' OR d.invalidated_at IS NOT NULL THEN RAISE EXCEPTION 'VALID_APPROVAL_REQUIRED'; END IF;
          IF d.environment<>p_environment THEN RAISE EXCEPTION 'PROMOTION_ENVIRONMENT_MISMATCH'; END IF;
          IF p_environment='production' AND (d.test_only OR NOT d.production_usable) THEN RAISE EXCEPTION 'TEST_APPROVAL_PRODUCTION_FORBIDDEN'; END IF;
          IF p_environment='p1_9_demo' AND NOT EXISTS(
            SELECT 1 FROM epidemic_discovery.p1_9_environment_markers m WHERE m.environment_type='p1_9_demo'
              AND m.database_name=current_database() AND m.disposable AND m.production_write_forbidden)
            THEN RAISE EXCEPTION 'DEMO_DATABASE_MARKER_MISSING'; END IF;
          SELECT id INTO out_id FROM epidemic_discovery.source_promotion_runs WHERE review_decision_id=p_decision;
          IF out_id IS NOT NULL THEN RETURN out_id; END IF;
          out_id=gen_random_uuid();
          INSERT INTO epidemic_discovery.source_promotion_runs(
            id,review_decision_id,state,promotion_key,environment)
          VALUES(out_id,p_decision,'PROMOTION_QUEUED',p_key,p_environment)
          ON CONFLICT(review_decision_id) DO NOTHING;
          GET DIAGNOSTICS inserted_count=ROW_COUNT;
          SELECT id INTO out_id FROM epidemic_discovery.source_promotion_runs WHERE review_decision_id=p_decision;
          IF inserted_count=1 THEN
            INSERT INTO epidemic_discovery.source_promotion_events(
              id,promotion_run_id,from_state,to_state,event_code,details,event_sha256)
            VALUES(gen_random_uuid(),out_id,NULL,'PROMOTION_QUEUED','EXPLICIT_PROMOTION_REQUESTED',
              jsonb_build_object('environment',p_environment),encode(digest(out_id::text||p_key,'sha256'),'hex'));
          END IF;
          RETURN out_id;
        END$$"""
    )
    op.execute(
        """CREATE VIEW epidemic_discovery.p1_9_review_cards_v1 AS
        SELECT v.*,d.id approval_id,d.decision,d.environment decision_environment,d.reviewer_kind,
          d.test_only,d.production_usable,d.binding_sha256,d.invalidated_at,d.invalidation_reason,
          p.id promotion_job_id,p.state promotion_state,p.environment promotion_environment,
          p.formal_data_source_id,p.normalization_run_id,p.formal_snapshot_id,p.publication_id
        FROM epidemic_discovery.review_candidate_cards_v2 v
        LEFT JOIN LATERAL(SELECT x.* FROM epidemic_discovery.source_review_decisions x
          WHERE x.review_case_id=v.review_case_id ORDER BY x.created_at DESC LIMIT 1)d ON true
        LEFT JOIN epidemic_discovery.source_promotion_runs p ON p.review_decision_id=d.id"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION epidemic_discovery.p1_9_submit_review_decision(uuid,text,text,text,uuid,char,text,text,boolean,jsonb,jsonb,jsonb,jsonb,char) FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON FUNCTION epidemic_discovery.p1_9_queue_promotion(uuid,text,char) FROM PUBLIC")
    op.execute(
        """DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
        GRANT SELECT ON epidemic_discovery.p1_9_review_cards_v1 TO epidemicreview;
        GRANT EXECUTE ON FUNCTION epidemic_discovery.p1_9_submit_review_decision(uuid,text,text,text,uuid,char,text,text,boolean,jsonb,jsonb,jsonb,jsonb,char) TO epidemicreview;
        GRANT EXECUTE ON FUNCTION epidemic_discovery.p1_9_queue_promotion(uuid,text,char) TO epidemicreview;
        END IF; END$$"""
    )
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON epidemic_discovery.p1_9_staging_observations TO epidemic_app")
    for table in (
        "p1_9_environment_markers",
        "p1_9_mapping_contracts",
        "p1_9_source_versions",
        "p1_9_publications",
        "p1_9_observation_lineage",
        "p1_9_review_cards_v1",
    ):
        op.execute(f"GRANT SELECT ON epidemic_discovery.{table} TO epidemic_app")
    for table in (
        "p1_9_mapping_contracts",
        "p1_9_source_versions",
        "p1_9_publications",
        "p1_9_observation_lineage",
    ):
        op.execute(f"GRANT INSERT ON epidemic_discovery.{table} TO epidemic_app")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic_discovery.p1_9_review_cards_v1")
    op.execute("DROP FUNCTION IF EXISTS epidemic_discovery.p1_9_queue_promotion(uuid,text,char)")
    op.execute(
        "DROP FUNCTION IF EXISTS epidemic_discovery.p1_9_submit_review_decision(uuid,text,text,text,uuid,char,text,text,boolean,jsonb,jsonb,jsonb,jsonb,char)"
    )
    for table in (
        "p1_9_observation_lineage",
        "p1_9_publications",
        "p1_9_staging_observations",
        "p1_9_source_versions",
        "p1_9_mapping_contracts",
        "p1_9_environment_markers",
    ):
        op.execute(f"DROP TABLE IF EXISTS epidemic_discovery.{table} CASCADE")
    op.execute(
        """ALTER TABLE epidemic_discovery.source_promotion_runs
        DROP COLUMN IF EXISTS environment,DROP COLUMN IF EXISTS claim_token,DROP COLUMN IF EXISTS claim_owner,
        DROP COLUMN IF EXISTS lease_expires_at,DROP COLUMN IF EXISTS heartbeat_at,DROP COLUMN IF EXISTS attempt_count,
        DROP COLUMN IF EXISTS max_attempts,DROP COLUMN IF EXISTS next_attempt_at,DROP COLUMN IF EXISTS last_error_code,
        DROP COLUMN IF EXISTS formal_data_source_id,DROP COLUMN IF EXISTS normalization_run_id,
        DROP COLUMN IF EXISTS publication_id"""
    )
    op.execute(
        """ALTER TABLE epidemic_discovery.source_review_decisions
        DROP COLUMN IF EXISTS environment,DROP COLUMN IF EXISTS reviewer_kind,DROP COLUMN IF EXISTS test_only,
        DROP COLUMN IF EXISTS production_usable,DROP COLUMN IF EXISTS binding_sha256,
        DROP COLUMN IF EXISTS license_evidence_sha256,DROP COLUMN IF EXISTS geographic_scope_decision,
        DROP COLUMN IF EXISTS metric_decision,DROP COLUMN IF EXISTS invalidated_at,DROP COLUMN IF EXISTS invalidation_reason"""
    )

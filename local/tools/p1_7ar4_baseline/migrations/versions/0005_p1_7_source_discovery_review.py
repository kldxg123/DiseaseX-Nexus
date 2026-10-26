# ruff: noqa: E501
"""Add isolated source discovery and human review domain.

Revision ID: 0005_p1_7_source_discovery_review
Revises: 0004_p1_6_latest_window_dashboard
"""

from alembic import op

revision = "0005_p1_7_source_discovery_review"
down_revision = "0004_p1_6_latest_window_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA epidemic_discovery AUTHORIZATION epidemicdb")
    op.execute("REVOKE ALL ON SCHEMA epidemic_discovery FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA epidemic_discovery TO epidemic_app")
    op.execute("""CREATE TABLE epidemic_discovery.discovery_runs(
      id uuid PRIMARY KEY,state text NOT NULL,state_version integer NOT NULL DEFAULT 1,
      request_json jsonb NOT NULL,request_sha256 char(64) NOT NULL UNIQUE,policy_version text NOT NULL,
      budget_json jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK(state IN ('QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','REVIEW_READY','NO_CANDIDATE','FAILED_RETRYABLE','FAILED_TERMINAL')),
      CHECK(request_sha256 ~ '^[0-9a-f]{64}$'))""")
    op.execute("""CREATE TABLE epidemic_discovery.discovery_run_events(
      id uuid PRIMARY KEY,discovery_run_id uuid NOT NULL REFERENCES epidemic_discovery.discovery_runs(id),
      from_state text,to_state text NOT NULL,event_code text NOT NULL,details jsonb NOT NULL DEFAULT '{}'::jsonb,
      event_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE epidemic_discovery.source_candidates(
      id uuid PRIMARY KEY,discovery_run_id uuid NOT NULL REFERENCES epidemic_discovery.discovery_runs(id),
      canonical_endpoint_sha256 char(64) NOT NULL,publisher text NOT NULL,dataset_name text NOT NULL,source_tier text NOT NULL,
      status text NOT NULL,risk_flags jsonb NOT NULL DEFAULT '[]'::jsonb,score numeric(7,4),score_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
      duplicate_source_key text,state_version integer NOT NULL DEFAULT 1,created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(discovery_run_id,canonical_endpoint_sha256))""")
    op.execute("""CREATE TABLE epidemic_discovery.source_candidate_versions(
      id uuid PRIMARY KEY,candidate_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidates(id),version_no integer NOT NULL,
      canonical_url_redacted text NOT NULL,canonical_endpoint_sha256 char(64) NOT NULL,publisher_evidence_sha256 char(64) NOT NULL,
      terms_sha256 char(64) NOT NULL,license_status text NOT NULL,format text NOT NULL,metadata jsonb NOT NULL,
      content_sha256 char(64) NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(candidate_id,version_no),UNIQUE(candidate_id,content_sha256))""")
    op.execute("""CREATE TABLE epidemic_discovery.quarantine_artifacts(
      id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
      sha256 char(64) NOT NULL UNIQUE,byte_size bigint NOT NULL,mime_type text NOT NULL,storage_key text NOT NULL UNIQUE,
      security_status text NOT NULL,security_report_sha256 char(64) NOT NULL,retrieved_at timestamptz NOT NULL,
      CHECK(byte_size BETWEEN 0 AND 52428800))""")
    op.execute("""CREATE TABLE epidemic_discovery.source_schema_profiles(
      id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
      schema_sha256 char(64) NOT NULL UNIQUE,row_count bigint NOT NULL,column_count integer NOT NULL,date_min date,date_max date,
      profile_json jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE epidemic_discovery.source_schema_columns(
      id uuid PRIMARY KEY,schema_profile_id uuid NOT NULL REFERENCES epidemic_discovery.source_schema_profiles(id),ordinal integer NOT NULL,
      source_name text NOT NULL,inferred_type text NOT NULL,example_value text,null_count bigint NOT NULL,warning_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
      UNIQUE(schema_profile_id,ordinal))""")
    op.execute("""CREATE TABLE epidemic_discovery.source_mapping_proposals(
      id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),revision integer NOT NULL,
      mapping_sha256 char(64) NOT NULL,proposal_json jsonb NOT NULL,status text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(candidate_version_id,revision),UNIQUE(candidate_version_id,mapping_sha256))""")
    op.execute("""CREATE TABLE epidemic_discovery.source_mapping_fields(
      id uuid PRIMARY KEY,mapping_proposal_id uuid NOT NULL REFERENCES epidemic_discovery.source_mapping_proposals(id),
      source_field text NOT NULL,canonical_field text,metric_code text,transform text NOT NULL,source_unit text,target_unit text,
      confidence numeric(5,4) NOT NULL,warning text,UNIQUE(mapping_proposal_id,source_field))""")
    op.execute("""CREATE TABLE epidemic_discovery.source_preview_rows(
      id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
      sample_kind text NOT NULL,row_ordinal bigint NOT NULL,raw_json jsonb NOT NULL,sanitized_json jsonb NOT NULL,
      row_errors jsonb NOT NULL DEFAULT '[]'::jsonb,row_sha256 char(64) NOT NULL,UNIQUE(candidate_version_id,sample_kind,row_ordinal))""")
    op.execute("""CREATE TABLE epidemic_discovery.source_quality_assessments(
      id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id),
      assessment_sha256 char(64) NOT NULL UNIQUE,status text NOT NULL,quality_json jsonb NOT NULL,security_json jsonb NOT NULL,
      pii_status text NOT NULL,license_status text NOT NULL,created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE epidemic_discovery.source_review_cases(
      id uuid PRIMARY KEY,candidate_version_id uuid NOT NULL UNIQUE REFERENCES epidemic_discovery.source_candidate_versions(id),
      state text NOT NULL,state_version integer NOT NULL DEFAULT 1,current_mapping_revision integer NOT NULL,
      etag char(64) NOT NULL,review_required boolean NOT NULL DEFAULT true,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE epidemic_discovery.source_review_decisions(
      id uuid PRIMARY KEY,review_case_id uuid NOT NULL REFERENCES epidemic_discovery.source_review_cases(id),
      decision text NOT NULL,scope text,reviewer_id text NOT NULL,reason text NOT NULL,idempotency_key uuid NOT NULL UNIQUE,
      evidence_json jsonb NOT NULL,evidence_sha256 char(64) NOT NULL,decision_sha256 char(64) NOT NULL UNIQUE,
      created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE epidemic_discovery.source_promotion_runs(
      id uuid PRIMARY KEY,review_decision_id uuid NOT NULL UNIQUE REFERENCES epidemic_discovery.source_review_decisions(id),
      state text NOT NULL,state_version integer NOT NULL DEFAULT 1,promotion_key char(64) NOT NULL UNIQUE,
      formal_snapshot_id uuid,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE epidemic_discovery.source_promotion_events(
      id uuid PRIMARY KEY,promotion_run_id uuid NOT NULL REFERENCES epidemic_discovery.source_promotion_runs(id),
      from_state text,to_state text NOT NULL,event_code text NOT NULL,details jsonb NOT NULL DEFAULT '{}'::jsonb,
      event_sha256 char(64) NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute(
        """CREATE FUNCTION epidemic_discovery.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'APPEND_ONLY';END$$"""
    )
    for table in (
        "discovery_run_events",
        "source_candidate_versions",
        "source_mapping_proposals",
        "source_review_decisions",
        "source_promotion_events",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON epidemic_discovery.{table} FOR EACH ROW EXECUTE FUNCTION epidemic_discovery.reject_mutation()"
        )
    op.execute("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA epidemic_discovery TO epidemic_app")
    for table in (
        "discovery_run_events",
        "source_candidate_versions",
        "source_mapping_proposals",
        "source_review_decisions",
        "source_promotion_events",
        "quarantine_artifacts",
        "source_schema_profiles",
        "source_schema_columns",
        "source_preview_rows",
        "source_quality_assessments",
        "source_mapping_fields",
    ):
        op.execute(f"REVOKE UPDATE,DELETE ON epidemic_discovery.{table} FROM epidemic_app")
    op.execute("""CREATE VIEW epidemic_discovery.review_cases_v AS
      SELECT rc.id AS review_case_id,rc.state,rc.state_version,rc.current_mapping_revision,rc.etag,
      rc.candidate_version_id,sc.publisher,sc.dataset_name,sc.source_tier,sc.status AS candidate_status,
      sc.risk_flags,sc.score,cv.license_status,cv.format,cv.metadata,sp.row_count,sp.column_count,
      qa.quality_json,qa.security_json,qa.pii_status,sc.discovery_run_id,
      cv.canonical_endpoint_sha256,cv.publisher_evidence_sha256,cv.terms_sha256,cv.content_sha256,
      sp.schema_sha256,mp.mapping_sha256,qa.assessment_sha256,qa.license_status AS assessed_license_status,
      ar.sha256 AS artifact_sha256,ar.security_report_sha256
      FROM epidemic_discovery.source_review_cases rc
      JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
      JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
      JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
      JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
      JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
      JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id""")
    op.execute("""CREATE VIEW epidemic_discovery.review_mapping_v AS
      SELECT rc.id AS review_case_id,mf.source_field,mf.canonical_field,mf.metric_code,mf.transform,
      mf.source_unit,mf.target_unit,mf.confidence,mf.warning
      FROM epidemic_discovery.source_mapping_fields mf
      JOIN epidemic_discovery.source_mapping_proposals mp ON mp.id=mf.mapping_proposal_id
      JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=mp.candidate_version_id
      WHERE mp.revision=rc.current_mapping_revision""")
    op.execute("""CREATE VIEW epidemic_discovery.review_preview_v AS
      SELECT rc.id AS review_case_id,pr.sample_kind,pr.row_ordinal,pr.sanitized_json,pr.row_errors,pr.row_sha256
      FROM epidemic_discovery.source_preview_rows pr
      JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=pr.candidate_version_id""")
    op.execute("""CREATE VIEW epidemic_discovery.review_schema_v AS
      SELECT rc.id AS review_case_id,sc.ordinal,sc.source_name,sc.inferred_type,sc.example_value,
      sc.null_count,sc.warning_flags
      FROM epidemic_discovery.source_schema_columns sc
      JOIN epidemic_discovery.source_schema_profiles sp ON sp.id=sc.schema_profile_id
      JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=sp.candidate_version_id""")
    op.execute("""CREATE FUNCTION epidemic_discovery.submit_review_decision(
      p_case uuid,p_action text,p_scope text,p_reviewer text,p_reason text,p_key uuid,
      p_etag char(64),p_evidence jsonb,p_evidence_sha char(64),p_decision_sha char(64)) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE c epidemic_discovery.source_review_cases%ROWTYPE; flags jsonb; out_id uuid;
      BEGIN
        SELECT rc.* INTO c FROM epidemic_discovery.source_review_cases rc WHERE rc.id=p_case FOR UPDATE;
        IF NOT FOUND OR c.state<>'REVIEW_READY' OR c.etag<>p_etag THEN RAISE EXCEPTION 'APPROVAL_STALE'; END IF;
        SELECT sc.risk_flags INTO flags FROM epidemic_discovery.source_candidate_versions cv
          JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id WHERE cv.id=c.candidate_version_id;
        IF flags ?| ARRAY['SSRF_BLOCKED','DNS_REBINDING_BLOCKED','ROBOTS_DISALLOWED','TOS_PROHIBITED',
          'LICENSE_PROHIBITED','AUTH_REQUIRED','CAPTCHA_REQUIRED','MALWARE_DETECTED','ACTIVE_CONTENT',
          'PERSON_LEVEL_DATA','SECRET_OR_CREDENTIAL_DETECTED'] THEN RAISE EXCEPTION 'HARD_BLOCK'; END IF;
        IF p_action NOT IN ('APPROVED','REJECTED','CHANGES_REQUESTED') THEN RAISE EXCEPTION 'INVALID_DECISION'; END IF;
        SELECT id INTO out_id FROM epidemic_discovery.source_review_decisions WHERE idempotency_key=p_key;
        IF out_id IS NOT NULL THEN RETURN out_id; END IF;
        out_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.source_review_decisions(id,review_case_id,decision,scope,reviewer_id,reason,
          idempotency_key,evidence_json,evidence_sha256,decision_sha256)
          VALUES(out_id,p_case,p_action,p_scope,p_reviewer,left(p_reason,500),p_key,p_evidence,p_evidence_sha,p_decision_sha);
        UPDATE epidemic_discovery.source_review_cases SET state=p_action,state_version=state_version+1,updated_at=now()
          WHERE id=p_case AND state_version=c.state_version;
        RETURN out_id;
      END$$""")
    op.execute(
        "REVOKE ALL ON FUNCTION epidemic_discovery.submit_review_decision(uuid,text,text,text,text,uuid,char,jsonb,char,char) FROM PUBLIC"
    )
    op.execute("""DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
      GRANT USAGE ON SCHEMA epidemic_discovery TO epidemicreview;
      GRANT SELECT ON epidemic_discovery.review_cases_v,epidemic_discovery.review_mapping_v,
        epidemic_discovery.review_preview_v,epidemic_discovery.review_schema_v TO epidemicreview;
      GRANT EXECUTE ON FUNCTION epidemic_discovery.submit_review_decision(uuid,text,text,text,text,uuid,char,jsonb,char,char) TO epidemicreview;
    END IF; END$$""")


def downgrade() -> None:
    op.execute("DROP SCHEMA epidemic_discovery CASCADE")

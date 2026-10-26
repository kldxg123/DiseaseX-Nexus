# ruff: noqa: E501
"""Route P1-12 discovery transitions through a restricted function.

Revision ID: 0023_p1_12_discovery_transition_guard
Revises: 0022_p1_12_review_ready_trusted_feeds
"""

from alembic import op

revision = "0023_p1_12_discovery_transition_guard"
down_revision = "0022_p1_12_review_ready_trusted_feeds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE VIEW epidemic_p1_12.review_discovery_queries_v2
        WITH (security_barrier=true) AS
        SELECT q.discovery_request_id,q.provider_id,q.language_tag,q.query_text,
               q.state,q.result_count,q.elapsed_ms,q.created_at,q.id
        FROM epidemic_p1_12.discovery_queries_v2 q
        WHERE EXISTS(
          SELECT 1 FROM epidemic_p1_12.review_ready_candidates_v2 r
          WHERE r.discovery_request_id=q.discovery_request_id
        )"""
    )
    op.execute("REVOKE ALL ON epidemic_p1_12.review_discovery_queries_v2 FROM PUBLIC")
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT SELECT ON epidemic_p1_12.review_discovery_queries_v2 TO epidemicreview;
        END IF;
        END$$"""
    )
    op.execute(
        """CREATE FUNCTION epidemic_p1_12.transition_discovery_run_v2(
        p_run uuid,p_expected text,p_state text,p_code text,p_details jsonb,p_progress jsonb
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,epidemic_p1_12,epidemic_discovery AS $$
        DECLARE old_state text; terminal boolean;
        BEGIN
          IF p_code IS NULL OR length(trim(p_code))=0 OR
             jsonb_typeof(COALESCE(p_details,'{}'::jsonb))<>'object' OR
             jsonb_typeof(COALESCE(p_progress,'{}'::jsonb))<>'object' THEN
            RAISE EXCEPTION 'INVALID_DISCOVERY_TRANSITION_ARGUMENT';
          END IF;
          SELECT state INTO old_state FROM epidemic_discovery.discovery_runs WHERE id=p_run FOR UPDATE;
          IF NOT FOUND OR old_state IS DISTINCT FROM p_expected THEN RETURN false; END IF;
          IF NOT ((old_state='QUEUED' AND p_state='CLAIMED') OR
                  (old_state='CLAIMED' AND p_state='SEARCHING') OR
                  (old_state='SEARCHING' AND p_state='SEARCH_COMPLETED') OR
                  (old_state='SEARCH_COMPLETED' AND p_state='SOURCE_FOUND') OR
                  (old_state='SOURCE_FOUND' AND p_state='FETCHING') OR
                  (old_state='FETCHING' AND p_state='FETCHED') OR
                  (old_state='FETCHED' AND p_state='PARSING') OR
                  (old_state='PARSING' AND p_state='PARSED') OR
                  (old_state='PARSED' AND p_state='VALIDATING') OR
                  (old_state='VALIDATING' AND p_state='COMPLETED_WITH_REVIEW_READY') OR
                  (p_state IN ('NO_CANDIDATE','FETCH_FAILED','PARSE_FAILED','INSUFFICIENT_STRUCTURED_DATA',
                               'PROVIDER_DEGRADED','BLOCKED_BY_SOURCE_POLICY','FAILED_RETRYABLE','FAILED_TERMINAL'))) THEN
            RAISE EXCEPTION 'ILLEGAL_DISCOVERY_TRANSITION';
          END IF;
          terminal=p_state IN ('COMPLETED_WITH_REVIEW_READY','NO_CANDIDATE','FETCH_FAILED','PARSE_FAILED',
                               'INSUFFICIENT_STRUCTURED_DATA','BLOCKED_BY_SOURCE_POLICY','FAILED_TERMINAL');
          UPDATE epidemic_discovery.discovery_runs SET state=p_state,state_version=state_version+1,
            progress_json=progress_json||COALESCE(p_progress,'{}'::jsonb),
            completed_at=CASE WHEN terminal THEN now() ELSE completed_at END,
            last_error_code=CASE WHEN p_state IN ('FETCH_FAILED','PARSE_FAILED','PROVIDER_DEGRADED','FAILED_RETRYABLE','FAILED_TERMINAL') THEN p_code ELSE NULL END,
            terminal_reason_code=CASE WHEN terminal AND p_state<>'COMPLETED_WITH_REVIEW_READY' THEN p_code ELSE NULL END,
            updated_at=now() WHERE id=p_run;
          INSERT INTO epidemic_discovery.discovery_run_events(
            id,discovery_run_id,from_state,to_state,event_code,details,event_sha256,requires_user_action)
          VALUES(gen_random_uuid(),p_run,old_state,p_state,p_code,COALESCE(p_details,'{}'::jsonb),
            encode(sha256(convert_to(p_run::text||old_state||p_state||p_code||clock_timestamp()::text,'UTF8')),'hex'),
            p_state IN ('INSUFFICIENT_STRUCTURED_DATA','BLOCKED_BY_SOURCE_POLICY','FAILED_TERMINAL'));
          RETURN true;
        END$$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION epidemic_p1_12.transition_discovery_run_v2(uuid,text,text,text,jsonb,jsonb) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION epidemic_p1_12.transition_discovery_run_v2(uuid,text,text,text,jsonb,jsonb) TO epidemic_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION epidemic_p1_12.transition_discovery_run_v2(uuid,text,text,text,jsonb,jsonb)")
    op.execute("DROP VIEW IF EXISTS epidemic_p1_12.review_discovery_queries_v2")

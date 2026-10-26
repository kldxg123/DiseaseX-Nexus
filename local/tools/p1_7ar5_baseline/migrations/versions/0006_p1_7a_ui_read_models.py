# ruff: noqa: E501
"""Add read models and least-privilege UI functions.

Revision ID: 0006_p1_7a_ui_read_models
Revises: 0005_p1_7_source_discovery_review
"""

from alembic import op

revision = "0006_p1_7a_ui_read_models"
down_revision = "0005_p1_7_source_discovery_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE VIEW epidemic.dashboard_history_series_v2 AS
      WITH ready_observations AS (
        SELECT DISTINCT sm.observation_id
        FROM epidemic.dataset_snapshot_observations sm
        JOIN epidemic.dashboard_snapshot_catalog_v1 cat ON cat.snapshot_id=sm.snapshot_id
      ), base AS (
        SELECT o.id observation_id,o.country_alpha2,c.alpha3 country_alpha3,c.name country_name,
          o.disease_id,d.slug disease_slug,o.metric_code,o.value_numeric,o.raw_value,o.period_start,o.period_end,
          o.granularity,o.revision,o.supersedes_observation_id,o.reporting_date,o.as_of,o.dimensions,o.dimensions_sha256,
          o.created_at,ds.source_key,ds.display_name source_name,m.unit,q.status quality_status,q.score quality_score,
          q.flags quality_flags,NOT (q.flags ?| ARRAY['LATEST_PERIOD_POSSIBLY_INCOMPLETE','RETROSPECTIVE_NEGATIVE_REVISION',
          'NEGATIVE_CUMULATIVE_VALUE','NEGATIVE_REPORTED_REVISION']) train_eligible,
          COALESCE(o.dimensions->>'location_code',o.dimensions->>'geo_id',o.country_alpha2) location_key,
          COALESCE(o.dimensions->>'geographic_level','country') geographic_level
        FROM ready_observations r JOIN epidemic.normalized_observations o ON o.id=r.observation_id
        JOIN epidemic.countries c ON c.alpha2=o.country_alpha2 JOIN epidemic.diseases d ON d.id=o.disease_id
        JOIN epidemic.source_fetches sf ON sf.id=o.source_fetch_id JOIN epidemic.retrieval_runs rr ON rr.id=sf.retrieval_run_id
        JOIN epidemic.data_sources ds ON ds.id=rr.source_id JOIN epidemic.metric_definitions m ON m.metric_code=o.metric_code
        JOIN epidemic.quality_assessments q ON q.observation_id=o.id
      ), ranked AS (
        SELECT base.*,count(*) OVER series revision_count,
          row_number() OVER (PARTITION BY source_key,location_key,geographic_level,disease_id,metric_code,granularity,
            period_start,period_end,dimensions_sha256 ORDER BY revision DESC,created_at DESC,observation_id DESC) latest_rank
        FROM base WINDOW series AS (PARTITION BY source_key,location_key,geographic_level,disease_id,metric_code,
          granularity,period_start,period_end,dimensions_sha256)
      ) SELECT *,revision_count-1 superseded_count FROM ranked WHERE latest_rank=1""")
    op.execute("""CREATE VIEW epidemic.dashboard_history_catalog_v2 AS
      WITH history AS (
        SELECT country_alpha2,country_alpha3,country_name,disease_id,disease_slug,source_key,source_name,
          count(*) observation_count,min(period_start) data_start,max(period_end) data_end
        FROM epidemic.dashboard_history_series_v2
        GROUP BY country_alpha2,country_alpha3,country_name,disease_id,disease_slug,source_key,source_name
      ), snaps AS (
        SELECT country_alpha2,disease_slug,source_key,count(DISTINCT snapshot_id) ready_snapshot_count,
          max(source_data_through) source_data_through,max(snapshot_created_at) latest_snapshot_created_at
        FROM epidemic.dashboard_snapshot_catalog_v1 GROUP BY country_alpha2,disease_slug,source_key
      ) SELECT h.*,s.ready_snapshot_count,s.source_data_through,s.latest_snapshot_created_at
        FROM history h JOIN snaps s USING(country_alpha2,disease_slug,source_key)""")
    op.execute("""CREATE VIEW epidemic.dashboard_catalog_options_v2 AS
      SELECT c.alpha2 country_iso2,c.alpha3 country_iso3,c.name country_name_en,
        COALESCE((SELECT ca.alias FROM epidemic.country_aliases ca WHERE ca.country_alpha2=c.alpha2
          AND ca.locale LIKE 'zh%' ORDER BY ca.alias_type,ca.id LIMIT 1),c.name) country_name_zh,
        d.id disease_id,d.slug disease_code,d.canonical_name disease_name_en,
        COALESCE((SELECT da.alias FROM epidemic.disease_aliases da WHERE da.disease_id=d.id
          AND da.language_tag LIKE 'zh%' ORDER BY da.alias_type,da.id LIMIT 1),d.canonical_name) disease_name_zh,
        (count(h.*)>0) has_data,COALESCE(sum(h.ready_snapshot_count),0)::bigint ready_snapshot_count,
        COALESCE(sum(h.observation_count),0)::bigint observation_count,min(h.data_start) data_start,
        max(h.data_end) data_end,max(h.source_data_through) source_data_through
      FROM epidemic.countries c CROSS JOIN epidemic.diseases d
      LEFT JOIN epidemic.dashboard_history_catalog_v2 h ON h.country_alpha2=c.alpha2 AND h.disease_id=d.id
      WHERE c.active AND d.active GROUP BY c.alpha2,c.alpha3,c.name,d.id,d.slug,d.canonical_name""")
    op.execute("""CREATE VIEW epidemic_discovery.review_mapping_integrity_v2 AS
      SELECT rc.id review_case_id,mp.id mapping_proposal_id,mp.revision,mp.status proposal_status,mf.source_field,
        'MAPPING_SOURCE_FIELD_MISSING' finding_code,true approval_blocked,
        '映射引用的来源字段不在真实 schema 中；不得静默替换。' finding_zh
      FROM epidemic_discovery.source_review_cases rc
      JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=rc.candidate_version_id
        AND mp.revision=rc.current_mapping_revision
      JOIN epidemic_discovery.source_mapping_fields mf ON mf.mapping_proposal_id=mp.id
      LEFT JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=rc.candidate_version_id
      LEFT JOIN epidemic_discovery.source_schema_columns sc ON sc.schema_profile_id=sp.id AND sc.source_name=mf.source_field
      WHERE sc.id IS NULL""")
    op.execute("""CREATE VIEW epidemic_discovery.review_field_mapping_v2 AS
      SELECT rc.id review_case_id,sc.ordinal,sc.source_name source_field,sc.example_value,sc.inferred_type,sc.null_count,
        sc.warning_flags,CASE WHEN mf.id IS NOT NULL THEN 'mapped' ELSE 'raw-only' END mapping_status,
        mf.canonical_field,mf.metric_code,mf.transform,mf.source_unit,mf.target_unit,mf.confidence,mf.warning
      FROM epidemic_discovery.source_review_cases rc
      JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=rc.candidate_version_id
      JOIN epidemic_discovery.source_schema_columns sc ON sc.schema_profile_id=sp.id
      LEFT JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=rc.candidate_version_id
        AND mp.revision=rc.current_mapping_revision
      LEFT JOIN epidemic_discovery.source_mapping_fields mf ON mf.mapping_proposal_id=mp.id AND mf.source_field=sc.source_name""")
    op.execute("""CREATE VIEW epidemic_discovery.review_candidate_cards_v2 AS
      SELECT v.*,COALESCE(v.metadata->>'geographic_scope','未说明') geographic_scope,
        v.metadata->>'latest_report_period' latest_report_period,v.metadata->>'update_frequency' update_frequency,
        COALESCE((SELECT count(*) FROM epidemic_discovery.review_mapping_integrity_v2 i WHERE i.review_case_id=v.review_case_id),0) mapping_error_count,
        true unsupported_geographic_grain,true approval_blocked,'NEEDS_IMPLEMENTATION' effective_status
      FROM epidemic_discovery.review_cases_v v""")
    op.execute("""CREATE VIEW epidemic_discovery.review_discovery_catalog_v2 AS
      SELECT c.alpha2 country_iso2,c.alpha3 country_iso3,c.name country_name_en,
        COALESCE((SELECT ca.alias FROM epidemic.country_aliases ca WHERE ca.country_alpha2=c.alpha2 AND ca.locale LIKE 'zh%'
          ORDER BY ca.alias_type,ca.id LIMIT 1),c.name) country_name_zh,
        d.id disease_id,d.slug disease_code,d.canonical_name disease_name_en,
        COALESCE((SELECT da.alias FROM epidemic.disease_aliases da WHERE da.disease_id=d.id AND da.language_tag LIKE 'zh%'
          ORDER BY da.alias_type,da.id LIMIT 1),d.canonical_name) disease_name_zh
      FROM epidemic.countries c CROSS JOIN epidemic.diseases d WHERE c.active AND d.active""")
    op.execute("""CREATE VIEW epidemic_discovery.review_discovery_runs_v2 AS
      SELECT id discovery_run_id,state,state_version,request_json,policy_version,created_at,updated_at,
        (SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id) candidate_count,
        (SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status='REVIEW_READY') review_ready_count
      FROM epidemic_discovery.discovery_runs dr""")
    op.execute("""CREATE VIEW epidemic_discovery.review_decision_history_v2 AS
      SELECT d.id decision_id,d.review_case_id,d.decision,d.scope,d.reviewer_id,d.reason,d.created_at,
        c.publisher,c.dataset_name FROM epidemic_discovery.source_review_decisions d
        JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id
        JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
        JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id""")
    op.execute("""CREATE FUNCTION epidemic_discovery.submit_structured_discovery(
      p_country_iso3 text,p_disease_id uuid,p_metrics text[],p_time_window text,p_tiers text[],
      p_languages text[],p_max_candidates integer) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE req jsonb; digest_sha text; run_id uuid; event_detail jsonb;
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM epidemic.countries WHERE alpha3=p_country_iso3 AND active) OR
          NOT EXISTS(SELECT 1 FROM epidemic.diseases WHERE id=p_disease_id AND active) THEN RAISE EXCEPTION 'INVALID_CATALOG_SELECTION'; END IF;
        IF p_metrics IS NULL OR NOT (p_metrics <@ ARRAY['cases_new','deaths_new','incidence_rate']::text[]) OR
          p_time_window NOT IN ('latest_rolling_year','latest_complete_calendar_year','current_year_to_latest_period') OR
          p_tiers IS NULL OR NOT (p_tiers <@ ARRAY['NATIONAL_HEALTH','GOVERNMENT_OPEN_DATA','REGIONAL_HEALTH','PUBLIC_HEALTH_RESEARCH']::text[]) OR
          p_max_candidates NOT BETWEEN 1 AND 20 THEN RAISE EXCEPTION 'INVALID_DISCOVERY_REQUEST'; END IF;
        req=jsonb_build_object('country_iso3',p_country_iso3,'disease_id',p_disease_id,'desired_metrics',p_metrics,
          'time_window_kind',p_time_window,'preferred_source_tiers',p_tiers,'language_preferences',COALESCE(p_languages,ARRAY['en']),
          'max_candidates',p_max_candidates);
        digest_sha=encode(sha256(convert_to(req::text,'UTF8')),'hex');
        SELECT id INTO run_id FROM epidemic_discovery.discovery_runs WHERE request_sha256=digest_sha;
        IF run_id IS NOT NULL THEN RETURN run_id; END IF;
        run_id=gen_random_uuid();
        INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json)
          VALUES(run_id,'QUEUED',req,digest_sha,'source-discovery-policy-v1',
          jsonb_build_object('queries',5,'results_per_query',20,'deep_candidates',5,'full_downloads',2,
          'http_requests',100,'file_bytes',52428800,'task_bytes',262144000,'global_concurrency',4,
          'domain_concurrency',1,'duration_seconds',900));
        event_detail=jsonb_build_object('origin','review_console_structured_form');
        INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256)
          VALUES(gen_random_uuid(),run_id,NULL,'QUEUED','DISCOVERY_QUEUED',event_detail,
          encode(sha256(convert_to(run_id::text||event_detail::text||clock_timestamp()::text,'UTF8')),'hex'));
        RETURN run_id;
      END$$""")
    op.execute("""CREATE FUNCTION epidemic_discovery.save_mapping_adjustment(
      p_case uuid,p_expected_revision integer,p_fields jsonb) RETURNS integer
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$
      DECLARE cv uuid; next_revision integer; proposal_id uuid; item jsonb; mapping_sha text;
      BEGIN
        SELECT candidate_version_id,current_mapping_revision INTO cv,next_revision FROM epidemic_discovery.source_review_cases
          WHERE id=p_case FOR UPDATE;
        IF cv IS NULL OR next_revision<>p_expected_revision THEN RAISE EXCEPTION 'MAPPING_STALE'; END IF;
        IF jsonb_typeof(p_fields)<>'array' OR jsonb_array_length(p_fields)>100 THEN RAISE EXCEPTION 'INVALID_MAPPING'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(p_fields) LOOP
          IF item->>'transform' NOT IN ('identity','parse_iso_date','parse_epiweek','parse_isoweek','parse_month',
            'country_name_to_iso3','numeric_count','numeric_rate','explicit_null_tokens') OR
            NOT EXISTS(SELECT 1 FROM epidemic_discovery.source_schema_profiles sp
              JOIN epidemic_discovery.source_schema_columns sc ON sc.schema_profile_id=sp.id
              WHERE sp.candidate_version_id=cv AND sc.source_name=item->>'source_field') THEN RAISE EXCEPTION 'INVALID_MAPPING_FIELD'; END IF;
        END LOOP;
        next_revision=next_revision+1;proposal_id=gen_random_uuid();
        mapping_sha=encode(sha256(convert_to(p_fields::text,'UTF8')),'hex');
        INSERT INTO epidemic_discovery.source_mapping_proposals(id,candidate_version_id,revision,mapping_sha256,proposal_json,status)
          VALUES(proposal_id,cv,next_revision,mapping_sha,p_fields,'PROPOSED_ADJUSTMENT');
        FOR item IN SELECT value FROM jsonb_array_elements(p_fields) LOOP
          INSERT INTO epidemic_discovery.source_mapping_fields(id,mapping_proposal_id,source_field,canonical_field,metric_code,
            transform,source_unit,target_unit,confidence,warning)
          VALUES(gen_random_uuid(),proposal_id,item->>'source_field',NULLIF(item->>'canonical_field',''),NULLIF(item->>'metric_code',''),
            item->>'transform',NULLIF(item->>'source_unit',''),NULLIF(item->>'target_unit',''),
            COALESCE((item->>'confidence')::numeric,0.5),NULLIF(item->>'warning',''));
        END LOOP;
        UPDATE epidemic_discovery.source_review_cases SET current_mapping_revision=next_revision,state_version=state_version+1,
          etag=encode(sha256(convert_to(etag||mapping_sha,'UTF8')),'hex'),updated_at=now() WHERE id=p_case;
        RETURN next_revision;
      END$$""")
    for view in (
        "dashboard_snapshot_catalog_v1",
        "dashboard_observation_series_v1",
        "dashboard_quality_summary_v1",
        "dashboard_history_series_v2",
        "dashboard_history_catalog_v2",
        "dashboard_catalog_options_v2",
    ):
        op.execute(f"GRANT SELECT ON epidemic.{view} TO epidemic_dashboard_ro")
    op.execute("""GRANT SELECT ON epidemic_discovery.review_mapping_integrity_v2,
      epidemic_discovery.review_field_mapping_v2,epidemic_discovery.review_candidate_cards_v2,
      epidemic_discovery.review_discovery_catalog_v2,epidemic_discovery.review_discovery_runs_v2,
      epidemic_discovery.review_decision_history_v2 TO epidemicreview""")
    op.execute(
        "REVOKE ALL ON FUNCTION epidemic_discovery.submit_structured_discovery(text,uuid,text[],text,text[],text[],integer) FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON FUNCTION epidemic_discovery.save_mapping_adjustment(uuid,integer,jsonb) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION epidemic_discovery.submit_structured_discovery(text,uuid,text[],text,text[],text[],integer) TO epidemicreview"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION epidemic_discovery.save_mapping_adjustment(uuid,integer,jsonb) TO epidemicreview"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION epidemic_discovery.save_mapping_adjustment(uuid,integer,jsonb)")
    op.execute(
        "DROP FUNCTION epidemic_discovery.submit_structured_discovery(text,uuid,text[],text,text[],text[],integer)"
    )
    for view in (
        "review_decision_history_v2",
        "review_discovery_runs_v2",
        "review_discovery_catalog_v2",
        "review_candidate_cards_v2",
        "review_field_mapping_v2",
        "review_mapping_integrity_v2",
    ):
        op.execute(f"DROP VIEW epidemic_discovery.{view}")
    for view in ("dashboard_catalog_options_v2", "dashboard_history_catalog_v2", "dashboard_history_series_v2"):
        op.execute(f"DROP VIEW epidemic.{view}")

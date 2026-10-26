from __future__ import annotations

# ruff: noqa: E501
from alembic import op

revision = "0020_p1_10r3_preview_permissions"
down_revision = "0019_p1_10r_formal_preview_http500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE CREATE ON SCHEMA epidemic_preview FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA epidemic_preview FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA epidemic_preview FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA epidemic_preview FROM PUBLIC")

    op.execute(
        """CREATE VIEW epidemic_preview.preview_candidate_version_history_v1 AS
        WITH base AS (
          SELECT sc.canonical_endpoint_sha256 logical_dataset_key,sc.id candidate_id,
            sc.publisher,sc.dataset_name,sc.status candidate_status,
            cv.id candidate_version_id,cv.version_no source_version_no,cv.canonical_url_redacted,
            cv.license_status,cv.content_sha256,cv.created_at version_created_at,
            rc.id review_case_id,rc.state review_state,rc.state_version,
            COALESCE(l.discovery_run_id,sc.discovery_run_id) discovery_run_id,
            dr.request_json->>'country_iso3' country_iso3,d.slug disease_code,
            ar.sha256 artifact_sha256,ar.retrieved_at,ar.byte_size,ar.mime_type,
            sp.schema_sha256,sp.row_count,sp.column_count,sp.date_min,sp.date_max,
            mp.mapping_sha256,qa.pii_status,qa.license_status assessed_license_status,
            f.status fetch_status,f.http_status,f.content_type fetch_content_type,
            f.byte_size fetch_byte_size,f.domain fetch_domain,f.elapsed_ms,
            f.attempted_at,f.completed_at,
            COALESCE((SELECT string_agg(c.source_name,',' ORDER BY c.ordinal)
              FROM epidemic_discovery.source_schema_columns c
              WHERE c.schema_profile_id=sp.id
                AND c.source_name IN ('dt_notific','sg_uf_not','id_municip','nu_ano')),'') safe_fields,
            pd.id preview_decision_id,pd.created_at preview_decided_at,
            pj.id preview_job_id,pj.state preview_state,pj.publication_id,
            pub.published_at preview_published_at
          FROM epidemic_discovery.source_candidates sc
          JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=sc.id
          JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id
          JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
          JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
          JOIN epidemic_discovery.source_mapping_proposals mp
            ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
          JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
          LEFT JOIN LATERAL (SELECT x.discovery_run_id FROM epidemic_discovery.discovery_run_candidate_links x
            WHERE x.candidate_version_id=cv.id ORDER BY x.linked_at DESC,x.id DESC LIMIT 1) l ON true
          JOIN epidemic_discovery.discovery_runs dr ON dr.id=COALESCE(l.discovery_run_id,sc.discovery_run_id)
          LEFT JOIN epidemic.diseases d ON d.id=CASE
            WHEN dr.request_json->>'disease_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN (dr.request_json->>'disease_id')::uuid ELSE NULL END
          LEFT JOIN epidemic_discovery.candidate_fetches f ON f.id=ar.fetch_id
          LEFT JOIN epidemic_preview.preview_review_decisions pd
            ON pd.review_case_id=rc.id AND pd.candidate_version_id=cv.id AND pd.artifact_sha256=ar.sha256
              AND pd.environment='production'
          LEFT JOIN epidemic_preview.preview_publication_jobs pj ON pj.preview_decision_id=pd.id
          LEFT JOIN epidemic_preview.preview_dashboard_publications pub ON pub.id=pj.publication_id
        ), ranked AS (
          SELECT base.*,
            row_number() OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) logical_version_no,
            row_number() OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at DESC,version_created_at DESC,candidate_version_id DESC) latest_rank,
            count(*) OVER (PARTITION BY logical_dataset_key) version_count,
            lag(artifact_sha256) OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) previous_artifact_sha256,
            lag(schema_sha256) OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) previous_schema_sha256,
            lag(row_count) OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) previous_row_count,
            lag(date_min) OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) previous_date_min,
            lag(date_max) OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) previous_date_max,
            lag(safe_fields) OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) previous_safe_fields,
            lag(mapping_sha256) OVER (PARTITION BY logical_dataset_key ORDER BY retrieved_at,version_created_at,candidate_version_id) previous_mapping_sha256
          FROM base
        )
        SELECT ranked.*,(latest_rank=1) is_current,(latest_rank>1) superseded,
          (previous_artifact_sha256 IS NOT NULL AND artifact_sha256<>previous_artifact_sha256) content_changed,
          CASE WHEN previous_row_count IS NULL THEN NULL ELSE row_count-previous_row_count END row_count_delta,
          (previous_schema_sha256 IS NOT NULL AND schema_sha256<>previous_schema_sha256) schema_changed,
          (previous_date_min IS NOT NULL AND (date_min<>previous_date_min OR date_max<>previous_date_max)) date_range_changed,
          (previous_safe_fields IS NOT NULL AND safe_fields<>previous_safe_fields) safe_fields_changed,
          (previous_mapping_sha256 IS NULL OR mapping_sha256=previous_mapping_sha256) mapping_still_valid,
          (artifact_sha256 IS NOT NULL AND schema_sha256 IS NOT NULL AND mapping_sha256 IS NOT NULL
            AND pii_status<>'BLOCKED' AND license_status NOT ILIKE '%PROHIBIT%') preview_eligible,
          concat_ws('；',
            CASE WHEN previous_artifact_sha256 IS NULL THEN '首个受控版本' ELSE '内容已变化' END,
            CASE WHEN previous_row_count IS NOT NULL THEN '行数变化 '||(row_count-previous_row_count)::text END,
            CASE WHEN previous_schema_sha256 IS NOT NULL THEN CASE WHEN schema_sha256<>previous_schema_sha256 THEN 'Schema 已变化' ELSE 'Schema 未变化' END END,
            CASE WHEN previous_date_min IS NOT NULL THEN CASE WHEN date_min<>previous_date_min OR date_max<>previous_date_max THEN '日期范围已变化' ELSE '日期范围未变化' END END,
            CASE WHEN previous_safe_fields IS NOT NULL THEN CASE WHEN safe_fields<>previous_safe_fields THEN '安全字段已变化' ELSE '安全字段未变化' END END,
            CASE WHEN previous_mapping_sha256 IS NOT NULL THEN CASE WHEN mapping_sha256=previous_mapping_sha256 THEN 'Mapping 仍有效' ELSE 'Mapping 需复核' END END
          ) difference_summary
        FROM ranked"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.preview_candidate_dataset_summary_v1 AS
        SELECT logical_dataset_key,publisher,dataset_name,country_iso3,disease_code,candidate_status,
          review_state,preview_state,logical_version_no current_version_no,version_count,
          candidate_version_id current_candidate_version_id,review_case_id current_review_case_id,
          artifact_sha256 current_artifact_sha256,retrieved_at current_retrieved_at,row_count,column_count,
          license_status,preview_eligible,difference_summary
        FROM epidemic_preview.preview_candidate_version_history_v1 WHERE is_current"""
    )
    op.execute(
        """CREATE VIEW epidemic_preview.preview_candidate_fetches_safe_v1 AS
        SELECT fetch_id,discovery_run_id,search_result_id,candidate_id,attempted_at,completed_at,
          domain,status,http_status,content_type,byte_size,elapsed_ms,artifact_sha256,failure_code
        FROM epidemic_discovery.review_candidate_fetches_v1"""
    )
    for view in (
        "preview_candidate_version_history_v1",
        "preview_candidate_dataset_summary_v1",
        "preview_candidate_fetches_safe_v1",
    ):
        op.execute(
            "DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicdb') THEN "
            f"ALTER VIEW epidemic_preview.{view} OWNER TO epidemicdb; END IF; END$$"
        )

    op.execute(
        """CREATE OR REPLACE VIEW epidemic_preview.runtime_readiness_v1 AS
        SELECT av.version_num alembic_revision,m.environment,m.database_name,m.production,m.disposable,
          (to_regclass('epidemic_preview.preview_review_cards_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.active_preview_publications_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_dashboard_series_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_lineage_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_safe_source_columns_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_discovery_candidate_links_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_candidate_version_history_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_candidate_dataset_summary_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_candidate_fetches_safe_v1') IS NOT NULL)
          required_objects_present
        FROM epidemic.alembic_version av
        CROSS JOIN epidemic_preview.environment_markers m"""
    )
    op.execute(
        """DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicdb') THEN
        ALTER VIEW epidemic_preview.runtime_readiness_v1 OWNER TO epidemicdb; END IF; END$$"""
    )

    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT USAGE ON SCHEMA epidemic_preview TO epidemicreview;
          GRANT SELECT ON epidemic_preview.preview_review_cards_v1,
            epidemic_preview.preview_safe_source_columns_v1,
            epidemic_preview.preview_discovery_candidate_links_v1,
            epidemic_preview.preview_candidate_version_history_v1,
            epidemic_preview.preview_candidate_dataset_summary_v1,
            epidemic_preview.preview_candidate_fetches_safe_v1,
            epidemic_preview.preview_review_decisions,
            epidemic_preview.preview_publication_jobs,
            epidemic_preview.preview_publication_events,
            epidemic_preview.runtime_readiness_v1 TO epidemicreview;
          REVOKE ALL ON epidemic_discovery.candidate_fetches FROM epidemicreview;
          GRANT EXECUTE ON FUNCTION epidemic_preview.submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,char,jsonb,char,text,boolean) TO epidemicreview;
          GRANT EXECUTE ON FUNCTION epidemic_preview.queue_preview_publication(uuid,uuid) TO epidemicreview;
          GRANT EXECUTE ON FUNCTION epidemic_preview.revoke_preview_publication(uuid,text,text,uuid) TO epidemicreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicpreview') THEN
          GRANT USAGE ON SCHEMA epidemic_preview,epidemic_discovery,epidemic TO epidemicpreview;
          GRANT SELECT ON epidemic.alembic_version TO epidemicpreview;
          GRANT SELECT ON epidemic_preview.preview_review_decisions,
            epidemic_preview.preview_publication_jobs,
            epidemic_preview.preview_publication_events,
            epidemic_preview.preview_mapping_contracts,
            epidemic_preview.preview_staging_observations,
            epidemic_preview.preview_observations,
            epidemic_preview.preview_quality_assessments,
            epidemic_preview.preview_snapshots,
            epidemic_preview.preview_snapshot_observations,
            epidemic_preview.preview_dashboard_publications TO epidemicpreview;
          GRANT INSERT ON epidemic_preview.preview_publication_events,
            epidemic_preview.preview_mapping_contracts,
            epidemic_preview.preview_staging_observations,
            epidemic_preview.preview_observations,
            epidemic_preview.preview_quality_assessments,
            epidemic_preview.preview_snapshots,
            epidemic_preview.preview_snapshot_observations,
            epidemic_preview.preview_dashboard_publications TO epidemicpreview;
          GRANT UPDATE ON epidemic_preview.preview_publication_jobs TO epidemicpreview;
          GRANT DELETE ON epidemic_preview.preview_staging_observations TO epidemicpreview;
          REVOKE UPDATE ON epidemic_preview.preview_review_decisions,
            epidemic_preview.preview_mapping_contracts,
            epidemic_preview.preview_observations,
            epidemic_preview.preview_quality_assessments,
            epidemic_preview.preview_snapshots,
            epidemic_preview.preview_snapshot_observations,
            epidemic_preview.preview_dashboard_publications FROM epidemicpreview;
          GRANT USAGE,SELECT ON SEQUENCE epidemic_preview.preview_publication_events_id_seq TO epidemicpreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
          GRANT USAGE ON SCHEMA epidemic_preview TO epidemic_dashboard_ro;
          GRANT SELECT ON epidemic_preview.active_preview_publications_v1,
            epidemic_preview.preview_dashboard_series_v1,
            epidemic_preview.preview_lineage_v1,
            epidemic_preview.runtime_readiness_v1 TO epidemic_dashboard_ro;
        END IF;
        END$$"""
    )

    op.execute(
        """DO $$BEGIN IF current_user='epidemicdb' THEN
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_preview GRANT SELECT,INSERT ON TABLES TO epidemicpreview';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_preview GRANT USAGE,SELECT ON SEQUENCES TO epidemicpreview';
        END IF; END$$"""
    )


def downgrade() -> None:
    op.execute(
        """DO $$BEGIN IF current_user='epidemicdb' THEN
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_preview REVOKE USAGE,SELECT ON SEQUENCES FROM epidemicpreview';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb IN SCHEMA epidemic_preview REVOKE SELECT,INSERT ON TABLES FROM epidemicpreview';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE epidemicdb GRANT EXECUTE ON FUNCTIONS TO PUBLIC';
        END IF; END$$"""
    )
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicpreview') THEN
          REVOKE SELECT ON epidemic.alembic_version FROM epidemicpreview;
          REVOKE USAGE ON SCHEMA epidemic FROM epidemicpreview;
          GRANT UPDATE ON epidemic_preview.preview_review_decisions,
            epidemic_preview.preview_mapping_contracts,
            epidemic_preview.preview_observations,
            epidemic_preview.preview_quality_assessments,
            epidemic_preview.preview_snapshots,
            epidemic_preview.preview_snapshot_observations,
            epidemic_preview.preview_dashboard_publications TO epidemicpreview;
        END IF;
        END$$"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW epidemic_preview.runtime_readiness_v1 AS
        SELECT av.version_num alembic_revision,m.environment,m.database_name,m.production,m.disposable,
          (to_regclass('epidemic_preview.preview_review_cards_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.active_preview_publications_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_dashboard_series_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_lineage_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_safe_source_columns_v1') IS NOT NULL
           AND to_regclass('epidemic_preview.preview_discovery_candidate_links_v1') IS NOT NULL)
          required_objects_present
        FROM epidemic.alembic_version av
        CROSS JOIN epidemic_preview.environment_markers m"""
    )
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_candidate_fetches_safe_v1")
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_candidate_dataset_summary_v1")
    op.execute("DROP VIEW IF EXISTS epidemic_preview.preview_candidate_version_history_v1")

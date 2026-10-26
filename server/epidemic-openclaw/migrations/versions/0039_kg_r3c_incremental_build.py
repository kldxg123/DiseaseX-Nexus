"""kg_r3c incremental KG build: import runs, works, assertions, graph versions,
data snapshots/profiles, model adapters, parameter bundles, score policies,
recommendations, review items, execution contracts.

Revision ID: 0039_kg_r3c_incremental_build
Revises: 0038_r3a_preexecution_scoring
Creates only NEW tables in epidemic_kg. Never alters or drops existing tables.
Idempotent: safe to run repeatedly (IF NOT EXISTS everywhere).
"""
from alembic import op

revision = "0039_kg_r3c_incremental_build"
down_revision = "0038_r3a_preexecution_scoring"
branch_labels = None
depends_on = None

SCHEMA = "epidemic_kg"


def upgrade():
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # --- import runs (immutable accounting of one archive import) ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_import_runs (
        import_run_id uuid PRIMARY KEY,
        import_key text NOT NULL UNIQUE,
        archive_path text NOT NULL,
        archive_sha256 text NOT NULL,
        status text NOT NULL DEFAULT 'OPEN',
        actor text NOT NULL DEFAULT 'kg_r3c_pipeline',
        extractor_versions jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        counts jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        started_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_import_members (
        member_id uuid PRIMARY KEY,
        import_run_id uuid NOT NULL REFERENCES {SCHEMA}.kg_import_runs(import_run_id),
        member_path text NOT NULL,
        inner_path text NOT NULL DEFAULT '',
        depth integer NOT NULL DEFAULT 1,
        member_sha256 text NOT NULL,
        size_bytes bigint NOT NULL,
        kind text NOT NULL,
        mime text NOT NULL,
        issues jsonb NOT NULL DEFAULT '[]'::jsonb,
        quarantined boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (import_run_id, member_path, inner_path)
    )""")

    # --- paper works & document versions ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_paper_works (
        work_id uuid PRIMARY KEY,
        normalized_identity text NOT NULL UNIQUE,
        identity_method text NOT NULL,
        title text,
        publication_year integer,
        authors jsonb NOT NULL DEFAULT '[]'::jsonb,
        doi text,
        pmid text,
        url text,
        item_type text,
        zotero_key text,
        metadata_source_member text,
        raw_metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        first_import_run_id uuid REFERENCES {SCHEMA}.kg_import_runs(import_run_id),
        created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute(f"CREATE INDEX IF NOT EXISTS kg_paper_works_doi_idx ON {SCHEMA}.kg_paper_works (doi)")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_document_versions (
        document_version_id uuid PRIMARY KEY,
        work_id uuid REFERENCES {SCHEMA}.kg_paper_works(work_id),
        fulltext_sha256 text NOT NULL UNIQUE,
        source_member_path text NOT NULL,
        source_inner_path text NOT NULL DEFAULT '',
        page_count integer,
        text_layer_status text,
        language text,
        parser_name text,
        parser_version text,
        ocr_engine text,
        ocr_confidence numeric,
        import_run_id uuid REFERENCES {SCHEMA}.kg_import_runs(import_run_id),
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    # --- assertions (all KG edges with provenance) ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_assertions (
        assertion_id uuid PRIMARY KEY,
        subject_kind text NOT NULL,
        subject_id text NOT NULL,
        predicate text NOT NULL,
        object_kind text,
        object_id text,
        value_text text,
        value_jsonb jsonb,
        polarity text NOT NULL DEFAULT 'SUPPORTS',
        scope jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        confidence numeric NOT NULL DEFAULT 0.5,
        review_status text NOT NULL DEFAULT 'MACHINE_EXTRACTED',
        extractor text NOT NULL,
        extractor_version text NOT NULL,
        import_run_id uuid REFERENCES {SCHEMA}.kg_import_runs(import_run_id),
        graph_version_id uuid,
        supersedes_id uuid,
        supersession_reason text,
        natural_key text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (natural_key, import_run_id)
    )""")
    op.execute(f"CREATE INDEX IF NOT EXISTS kg_assertions_subject_idx ON {SCHEMA}.kg_assertions (subject_kind, subject_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS kg_assertions_predicate_idx ON {SCHEMA}.kg_assertions (predicate)")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_assertion_evidence (
        assertion_id uuid NOT NULL REFERENCES {SCHEMA}.kg_assertions(assertion_id),
        evidence_span_id uuid NOT NULL REFERENCES {SCHEMA}.kg_evidence_spans(evidence_span_id),
        binding_role text NOT NULL DEFAULT 'DIRECT',
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (assertion_id, evidence_span_id)
    )""")

    # --- graph versions (immutable published snapshots) ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_graph_versions (
        graph_version_id uuid PRIMARY KEY,
        import_run_id uuid NOT NULL REFERENCES {SCHEMA}.kg_import_runs(import_run_id),
        parent_graph_version_id uuid,
        document_set_hash text NOT NULL,
        assertion_set_hash text NOT NULL,
        extractor_versions jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        review_policy text NOT NULL DEFAULT 'MACHINE_EXTRACTED_REVIEW_REQUIRED',
        status text NOT NULL DEFAULT 'PUBLISHED',
        integrity_report jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        published_at timestamptz NOT NULL DEFAULT now()
    )""")

    # --- data snapshots / profiles / forecast tasks ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_data_snapshots (
        snapshot_id uuid PRIMARY KEY,
        country text NOT NULL,
        disease text NOT NULL,
        indicator text NOT NULL,
        frequency text NOT NULL,
        source_table text NOT NULL,
        n_obs integer NOT NULL,
        first_date date,
        last_date date,
        series_sha256 text NOT NULL,
        snapshot_sha256 text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (country, disease, indicator, frequency, series_sha256)
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_data_profiles (
        profile_id uuid PRIMARY KEY,
        snapshot_id uuid NOT NULL REFERENCES {SCHEMA}.kg_data_snapshots(snapshot_id),
        profile_json jsonb NOT NULL,
        profile_sha256 text NOT NULL UNIQUE,
        profiler_version text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_forecast_tasks (
        task_id uuid PRIMARY KEY,
        profile_id uuid NOT NULL REFERENCES {SCHEMA}.kg_data_profiles(profile_id),
        horizons jsonb NOT NULL DEFAULT '["H1","H2","H3","H4"]'::jsonb,
        target_semantics text NOT NULL,
        leakage_boundary text,
        task_sha256 text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    # --- model adapters & parameter bundles ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_model_adapters (
        adapter_id text PRIMARY KEY,
        model_family text NOT NULL,
        adapter_version text NOT NULL,
        module_path text NOT NULL,
        is_baseline boolean NOT NULL DEFAULT false,
        executable boolean NOT NULL DEFAULT true,
        capabilities jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        data_requirements jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        registered_at timestamptz NOT NULL DEFAULT now()
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_parameter_bundles (
        bundle_id uuid PRIMARY KEY,
        adapter_id text NOT NULL REFERENCES {SCHEMA}.kg_model_adapters(adapter_id),
        profile_id uuid REFERENCES {SCHEMA}.kg_data_profiles(profile_id),
        bundle_version text NOT NULL,
        parameters jsonb NOT NULL,
        completeness numeric NOT NULL,
        source_tiers jsonb NOT NULL DEFAULT '[]'::jsonb,
        bundle_sha256 text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    # --- score policies & recommendations ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_score_policies (
        policy_id text PRIMARY KEY,
        policy_version text NOT NULL,
        dimensions jsonb NOT NULL,
        thresholds jsonb NOT NULL,
        penalties jsonb NOT NULL DEFAULT '[]'::jsonb,
        status text NOT NULL DEFAULT 'FROZEN',
        policy_sha256 text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_recommendation_runs (
        run_id uuid PRIMARY KEY,
        graph_version_id uuid NOT NULL REFERENCES {SCHEMA}.kg_graph_versions(graph_version_id),
        policy_id text NOT NULL REFERENCES {SCHEMA}.kg_score_policies(policy_id),
        profile_set_hash text NOT NULL,
        score_type text NOT NULL DEFAULT 'PRE_EXECUTION_EVIDENCE_SCORE',
        status text NOT NULL DEFAULT 'COMPLETE',
        actually_executed boolean NOT NULL DEFAULT false,
        forecast_run_id uuid,
        execution_status text NOT NULL DEFAULT 'DEFERRED_NOT_EXECUTED',
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_recommendations (
        recommendation_id uuid PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES {SCHEMA}.kg_recommendation_runs(run_id),
        profile_id uuid NOT NULL REFERENCES {SCHEMA}.kg_data_profiles(profile_id),
        adapter_id text REFERENCES {SCHEMA}.kg_model_adapters(adapter_id),
        bundle_id uuid REFERENCES {SCHEMA}.kg_parameter_bundles(bundle_id),
        is_baseline boolean NOT NULL DEFAULT false,
        total_score numeric,
        parameter_confidence_score numeric,
        recomputed_total numeric,
        recomputed_param numeric,
        rank integer,
        eligibility_status text NOT NULL,
        ineligibility_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
        gap_list jsonb NOT NULL DEFAULT '[]'::jsonb,
        tie_break_key text,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (run_id, profile_id, adapter_id)
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_recommendation_components (
        component_id uuid PRIMARY KEY,
        recommendation_id uuid NOT NULL REFERENCES {SCHEMA}.kg_recommendations(recommendation_id),
        dimension text NOT NULL,
        rule_id text NOT NULL,
        raw_value text,
        score numeric NOT NULL,
        max_score numeric NOT NULL,
        is_penalty boolean NOT NULL DEFAULT false,
        rationale text NOT NULL,
        source_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (recommendation_id, dimension, rule_id)
    )""")

    # --- review queue ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_review_items (
        review_item_id uuid PRIMARY KEY,
        item_kind text NOT NULL,
        target_id text NOT NULL,
        reason text NOT NULL,
        status text NOT NULL DEFAULT 'OPEN',
        payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        import_run_id uuid REFERENCES {SCHEMA}.kg_import_runs(import_run_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (item_kind, target_id, reason, import_run_id)
    )""")

    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_review_decisions (
        decision_id uuid PRIMARY KEY,
        review_item_id uuid NOT NULL REFERENCES {SCHEMA}.kg_review_items(review_item_id),
        decision text NOT NULL,
        actor text NOT NULL,
        rationale text,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    # --- execution contracts (v1; creation only, execution deferred) ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_execution_contracts (
        contract_id uuid PRIMARY KEY,
        recommendation_id uuid NOT NULL REFERENCES {SCHEMA}.kg_recommendations(recommendation_id),
        contract_version text NOT NULL DEFAULT 'v1',
        contract jsonb NOT NULL,
        contract_sha256 text NOT NULL UNIQUE,
        status text NOT NULL DEFAULT 'DEFERRED_NOT_EXECUTED',
        created_at timestamptz NOT NULL DEFAULT now()
    )""")

    # --- audit log for write APIs ---
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.kg_api_audit_log (
        audit_id uuid PRIMARY KEY,
        endpoint text NOT NULL,
        method text NOT NULL,
        actor text NOT NULL,
        idempotency_key text,
        request_hash text NOT NULL,
        response_status integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (endpoint, idempotency_key)
    )""")

    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO epidemic_dashboard_ro")


def downgrade():
    # Intentionally no destructive downgrade: tables are additive-only.
    pass

# ruff: noqa: E501
"""kg_r3c dashboard routes.

Read APIs go through the existing read-only SQLAlchemy engine.
Write APIs use the local unix-socket ``epidemic_app`` connection (TRUST auth,
no password) and are idempotent: every write is recorded in
``epidemic_kg.kg_api_audit_log`` and a duplicate ``Idempotency-Key`` replays the
original response without re-applying side effects.

All scores exposed here are PRE_EXECUTION_EVIDENCE_SCORE values; nothing on this
surface executes models. Execution contracts are created with status
DEFERRED_NOT_EXECUTED only.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine, text

SCHEMA = "epidemic_kg"
AUDIT_ACTOR = "dashboard_user"
SCORE_TYPE = "PRE_EXECUTION_EVIDENCE_SCORE"
EXECUTION_STATUS = "DEFERRED_NOT_EXECUTED"
HORIZONS = ["H1", "H2", "H3", "H4"]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__str__") and value.__class__.__module__ == "decimal":
        return float(value)
    return value


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(payload) or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_conn():
    """Open a write-capable connection via the local unix socket (TRUST auth).

    Imported lazily so the module loads without psycopg / the runtime path.
    """
    import psycopg
    from psycopg.rows import dict_row

    from epidemic_core.kg_r3c.dbutil import SOCK

    override = __import__("os").environ.get("KG_WRITE_DATABASE_URL")
    if override:
        return psycopg.connect(override, row_factory=dict_row)
    return psycopg.connect(host=SOCK, port=55432, user="epidemic_app", dbname="epidemic_openclaw", row_factory=dict_row)


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def install_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    json_response: Callable[[Any, int], Response],
) -> None:

    def _replay(audit_id: str) -> JSONResponse:
        return JSONResponse(
            {"idempotent_replay": True, "audit_id": audit_id},
            status_code=200,
            headers={"Idempotent-Replay": "true"},
        )

    def _audit_replay(conn: Any, endpoint: str, idempotency_key: str | None) -> str | None:
        if not idempotency_key:
            return None
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT audit_id FROM {SCHEMA}.kg_api_audit_log WHERE endpoint=%s AND idempotency_key=%s",
                (endpoint, idempotency_key),
            )
            row = cur.fetchone()
        return str(row["audit_id"]) if row else None

    def _audit_record(conn: Any, endpoint: str, method: str, idempotency_key: str | None, request_hash: str, response_status: int) -> str:
        audit_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {SCHEMA}.kg_api_audit_log
                    (audit_id, endpoint, method, actor, idempotency_key, request_hash, response_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (endpoint, idempotency_key) DO NOTHING""",
                (audit_id, endpoint, method, AUDIT_ACTOR, idempotency_key, request_hash, response_status),
            )
        return audit_id

    # ---------------------------------------------------------------- reads
    @app.get("/api/v1/kg/import-runs")
    def kg_r3c_import_runs(request: Request, limit: int = 50) -> Response:
        if not authorized(request):
            return unauthorized()
        limit = max(1, min(int(limit), 200))
        with engine.connect() as connection:
            rows = connection.execute(
                text(f"""
                    select r.import_run_id::text, r.import_key, r.status, r.actor,
                           r.archive_sha256, r.counts, r.started_at, r.finished_at,
                           (select count(*)::int from {SCHEMA}.kg_import_members m
                             where m.import_run_id = r.import_run_id) as member_count,
                           (select count(*)::int from {SCHEMA}.kg_import_members m
                             where m.import_run_id = r.import_run_id and m.quarantined) as quarantined_count
                    from {SCHEMA}.kg_import_runs r
                    order by r.started_at desc
                    limit :limit
                """),
                {"limit": limit},
            ).mappings().all()
        return json_response(_jsonable({"items": [dict(r) for r in rows]}), 200)

    @app.get("/api/v1/kg/import-runs/{import_run_id}")
    def kg_r3c_import_run_detail(request: Request, import_run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(import_run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            run = connection.execute(
                text(f"""
                    select import_run_id::text, import_key, archive_path, archive_sha256,
                           status, actor, extractor_versions, counts, started_at, finished_at
                    from {SCHEMA}.kg_import_runs where import_run_id = cast(:id as uuid)
                """),
                {"id": import_run_id},
            ).mappings().first()
            if not run:
                return json_response({"detail": "IMPORT_RUN_NOT_FOUND"}, 404)
            members = connection.execute(
                text(f"""
                    select kind, count(*)::int as member_count,
                           coalesce(sum(size_bytes), 0)::bigint as total_bytes,
                           count(*) filter (where quarantined)::int as quarantined_count
                    from {SCHEMA}.kg_import_members
                    where import_run_id = cast(:id as uuid)
                    group by kind order by kind
                """),
                {"id": import_run_id},
            ).mappings().all()
        return json_response(_jsonable({"import_run": dict(run), "member_accounting": [dict(r) for r in members]}), 200)

    @app.get("/api/v1/kg/review-items")
    def kg_r3c_review_items(request: Request, status: str | None = None, limit: int = 50) -> Response:
        if not authorized(request):
            return unauthorized()
        limit = max(1, min(int(limit), 200))
        params = {"status": status, "limit": limit}
        with engine.connect() as connection:
            rows = connection.execute(
                text(f"""
                    select review_item_id::text, item_kind, target_id, reason, status,
                           payload, import_run_id::text, created_at
                    from {SCHEMA}.kg_review_items
                    where (cast(:status as text) is null or status = cast(:status as text))
                    order by created_at desc
                    limit :limit
                """),
                params,
            ).mappings().all()
            counts = connection.execute(
                text(f"select status, count(*)::int as n from {SCHEMA}.kg_review_items group by status order by status")
            ).mappings().all()
        return json_response(_jsonable({
            "items": [dict(r) for r in rows],
            "status_counts": {str(c["status"]): c["n"] for c in counts},
        }), 200)

    @app.get("/api/v1/kg/graph-versions")
    def kg_r3c_graph_versions(request: Request, limit: int = 20) -> Response:
        if not authorized(request):
            return unauthorized()
        limit = max(1, min(int(limit), 100))
        with engine.connect() as connection:
            rows = connection.execute(
                text(f"""
                    select graph_version_id::text, import_run_id::text, parent_graph_version_id::text,
                           document_set_hash, assertion_set_hash, extractor_versions, review_policy,
                           status, integrity_report, published_at
                    from {SCHEMA}.kg_graph_versions
                    order by published_at desc
                    limit :limit
                """),
                {"limit": limit},
            ).mappings().all()
        return json_response(_jsonable({"items": [dict(r) for r in rows]}), 200)

    @app.get("/api/v1/kg/graph-versions/{graph_version_id}")
    def kg_r3c_graph_version_detail(request: Request, graph_version_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(graph_version_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            row = connection.execute(
                text(f"""
                    select graph_version_id::text, import_run_id::text, parent_graph_version_id::text,
                           document_set_hash, assertion_set_hash, extractor_versions, review_policy,
                           status, integrity_report, published_at
                    from {SCHEMA}.kg_graph_versions where graph_version_id = cast(:id as uuid)
                """),
                {"id": graph_version_id},
            ).mappings().first()
        if not row:
            return json_response({"detail": "GRAPH_VERSION_NOT_FOUND"}, 404)
        return json_response(_jsonable({"graph_version": dict(row)}), 200)

    @app.get("/api/v1/kg/subgraphs")
    def kg_r3c_subgraph(request: Request, subject_kind: str, subject_id: str, depth: int = 2) -> Response:
        if not authorized(request):
            return unauthorized()
        depth = max(1, min(int(depth), 4))
        params = {"sk": subject_kind, "sid": subject_id, "depth": depth}
        with engine.connect() as connection:
            rows = connection.execute(
                text(f"""
                    with recursive walk as (
                        select assertion_id::text as assertion_id, subject_kind, subject_id,
                               predicate, object_kind, object_id, value_text, polarity,
                               confidence, review_status, 1 as depth
                        from {SCHEMA}.kg_assertions
                        where subject_kind = :sk and subject_id = :sid
                        union
                        select a.assertion_id::text, a.subject_kind, a.subject_id,
                               a.predicate, a.object_kind, a.object_id, a.value_text, a.polarity,
                               a.confidence, a.review_status, w.depth + 1
                        from {SCHEMA}.kg_assertions a
                        join walk w on a.subject_id = w.object_id
                                   and (w.object_kind is null or a.subject_kind = w.object_kind)
                        where w.depth < :depth
                    )
                    select assertion_id, subject_kind, subject_id, predicate, object_kind,
                           object_id, value_text, polarity, confidence, review_status, depth
                    from walk order by depth, predicate limit 500
                """),
                params,
            ).mappings().all()
        return json_response(_jsonable({
            "subject": {"kind": subject_kind, "id": subject_id},
            "depth": depth,
            "assertions": [dict(r) for r in rows],
        }), 200)

    @app.get("/api/v1/kg/papers/{work_id}/evidence-spans")
    def kg_r3c_paper_evidence_spans(request: Request, work_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(work_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            work = connection.execute(
                text(f"""
                    select work_id::text, normalized_identity, title, publication_year,
                           doi, pmid, url, item_type
                    from {SCHEMA}.kg_paper_works where work_id = cast(:id as uuid)
                """),
                {"id": work_id},
            ).mappings().first()
            if not work:
                return json_response({"detail": "WORK_NOT_FOUND"}, 404)
            versions = connection.execute(
                text(f"""
                    select document_version_id::text, fulltext_sha256, source_member_path,
                           page_count, text_layer_status, language, parser_name, parser_version,
                           ocr_engine, ocr_confidence
                    from {SCHEMA}.kg_document_versions where work_id = cast(:id as uuid)
                    order by created_at
                """),
                {"id": work_id},
            ).mappings().all()
            version_ids = [v["document_version_id"] for v in versions]
            spans: list[dict[str, Any]] = []
            if version_ids:
                spans = connection.execute(
                    text(f"""
                        select evidence_span_id::text, document_node_id::text, physical_page,
                               printed_page_label, section_label, table_or_figure, raw_anchor,
                               anchor_start, anchor_end, anchor_sha256, source_pdf_sha256,
                               evidence_eligible, quarantine,
                               properties->>'document_version_id' as document_version_id
                        from {SCHEMA}.kg_evidence_spans
                        where properties->>'document_version_id' = any(:ids)
                        order by document_node_id, physical_page, anchor_start
                        limit 500
                    """),
                    {"ids": version_ids},
                ).mappings().all()
        return json_response(_jsonable({
            "work": dict(work),
            "document_versions": [dict(v) for v in versions],
            "evidence_spans": [dict(s) for s in spans],
        }), 200)

    @app.get("/api/v1/kg/data-profiles/{profile_id}")
    def kg_r3c_data_profile_detail(request: Request, profile_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(profile_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            row = connection.execute(
                text(f"""
                    select p.profile_id::text, p.profile_sha256, p.profiler_version, p.profile_json,
                           p.created_at, s.snapshot_id::text, s.country, s.disease, s.indicator,
                           s.frequency, s.source_table, s.n_obs, s.first_date, s.last_date,
                           s.series_sha256, s.snapshot_sha256
                    from {SCHEMA}.kg_data_profiles p
                    join {SCHEMA}.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
                    where p.profile_id = cast(:id as uuid)
                """),
                {"id": profile_id},
            ).mappings().first()
            if not row:
                return json_response({"detail": "PROFILE_NOT_FOUND"}, 404)
            task = connection.execute(
                text(f"""
                    select task_id::text, horizons, target_semantics, leakage_boundary, task_sha256
                    from {SCHEMA}.kg_forecast_tasks where profile_id = cast(:id as uuid)
                """),
                {"id": profile_id},
            ).mappings().first()
        return json_response(_jsonable({"profile": dict(row), "forecast_task": dict(task) if task else None}), 200)

    @app.get("/api/v1/kg/r3c/catalog")
    def kg_r3c_catalog(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        with engine.connect() as connection:
            rows = connection.execute(
                text(f"""
                    select distinct s.country, s.disease, s.indicator, s.frequency
                    from {SCHEMA}.kg_recommendations r
                    join {SCHEMA}.kg_data_profiles p on p.profile_id = r.profile_id
                    join {SCHEMA}.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
                    where r.is_baseline = false
                    order by s.country, s.disease, s.indicator, s.frequency
                """)
            ).mappings().all()
        return json_response(_jsonable({
            "score_type": SCORE_TYPE,
            "execution_status": EXECUTION_STATUS,
            "items": [dict(r) for r in rows],
        }), 200)

    @app.get("/api/v1/kg/r3c/recommendations")
    def kg_r3c_recommendations(
        request: Request,
        country: str | None = None,
        disease: str | None = None,
        indicator: str | None = None,
        frequency: str | None = None,
        include_baselines: bool = False,
        eligible_only: bool = False,
        limit: int = 100,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        limit = max(1, min(int(limit), 200))
        params = {
            "country": country,
            "disease": disease,
            "indicator": indicator,
            "frequency": frequency,
            "include_baselines": include_baselines,
            "limit": limit,
        }
        eligible_clause = "and r.eligibility_status = 'ELIGIBLE_RECOMMENDATION'" if eligible_only else ""
        with engine.connect() as connection:
            rows = connection.execute(
                text(f"""
                    select r.recommendation_id::text, r.run_id::text, r.profile_id::text,
                           r.adapter_id, r.bundle_id::text, r.is_baseline, r.total_score,
                           r.parameter_confidence_score, r.rank, r.eligibility_status,
                           r.ineligibility_reasons, r.gap_list,
                           s.country, s.disease, s.indicator, s.frequency,
                           a.model_family, a.adapter_version,
                           b.bundle_sha256, b.completeness as bundle_completeness
                    from {SCHEMA}.kg_recommendations r
                    join {SCHEMA}.kg_data_profiles p on p.profile_id = r.profile_id
                    join {SCHEMA}.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
                    left join {SCHEMA}.kg_model_adapters a on a.adapter_id = r.adapter_id
                    left join {SCHEMA}.kg_parameter_bundles b on b.bundle_id = r.bundle_id
                    where (cast(:country as text) is null or s.country = cast(:country as text))
                      and (cast(:disease as text) is null or s.disease = cast(:disease as text))
                      and (cast(:indicator as text) is null or s.indicator = cast(:indicator as text))
                      and (cast(:frequency as text) is null or s.frequency = cast(:frequency as text))
                      and (cast(:include_baselines as boolean) = true or r.is_baseline = false)
                      {eligible_clause}
                    order by r.rank nulls last, r.total_score desc nulls last
                    limit :limit
                """),
                params,
            ).mappings().all()
        return json_response(_jsonable({
            "score_type": SCORE_TYPE,
            "execution_status": EXECUTION_STATUS,
            "items": [dict(r) for r in rows],
        }), 200)

    @app.get("/api/v1/kg/r3c/recommendations/{recommendation_id}")
    def kg_r3c_recommendation_detail(request: Request, recommendation_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(recommendation_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            rec = connection.execute(
                text(f"""
                    select r.recommendation_id::text, r.run_id::text, r.profile_id::text,
                           r.adapter_id, r.bundle_id::text, r.is_baseline, r.total_score,
                           r.parameter_confidence_score, r.recomputed_total, r.recomputed_param,
                           r.rank, r.eligibility_status, r.ineligibility_reasons, r.gap_list,
                           r.created_at,
                           s.country, s.disease, s.indicator, s.frequency,
                           s.snapshot_sha256, p.profile_sha256,
                           run.score_type, run.execution_status, run.actually_executed,
                           run.forecast_run_id::text, run.profile_set_hash,
                           run.graph_version_id::text, run.policy_id
                    from {SCHEMA}.kg_recommendations r
                    join {SCHEMA}.kg_data_profiles p on p.profile_id = r.profile_id
                    join {SCHEMA}.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
                    join {SCHEMA}.kg_recommendation_runs run on run.run_id = r.run_id
                    where r.recommendation_id = cast(:id as uuid)
                """),
                {"id": recommendation_id},
            ).mappings().first()
            if not rec:
                return json_response({"detail": "RECOMMENDATION_NOT_FOUND"}, 404)
            components = connection.execute(
                text(f"""
                    select component_id::text, dimension, rule_id, raw_value, score,
                           max_score, is_penalty, rationale, source_ids
                    from {SCHEMA}.kg_recommendation_components
                    where recommendation_id = cast(:id as uuid)
                    order by dimension, rule_id
                """),
                {"id": recommendation_id},
            ).mappings().all()
            evidence_ids = sorted({
                str(source_id)
                for component in components
                for source_id in (component.get("source_ids") or [])
                if _valid_uuid(str(source_id))
            })
            evidence_spans: list[dict[str, Any]] = []
            if evidence_ids:
                evidence_spans = connection.execute(
                    text(f"""
                        select evidence_span_id::text, document_node_id::text, physical_page,
                               printed_page_label, section_label, table_or_figure, raw_anchor,
                               anchor_start, anchor_end, anchor_sha256, source_pdf_sha256,
                               evidence_eligible, quarantine,
                               properties->>'document_version_id' as document_version_id
                        from {SCHEMA}.kg_evidence_spans
                        where evidence_span_id::text = any(:ids)
                        order by evidence_span_id
                    """),
                    {"ids": evidence_ids},
                ).mappings().all()
            bundle = adapter = policy = graph_version = None
            if rec["bundle_id"]:
                bundle = connection.execute(
                    text(f"""
                        select bundle_id::text, adapter_id, bundle_version, parameters,
                               completeness, source_tiers, bundle_sha256
                        from {SCHEMA}.kg_parameter_bundles where bundle_id = cast(:id as uuid)
                    """),
                    {"id": rec["bundle_id"]},
                ).mappings().first()
            if rec["adapter_id"]:
                adapter = connection.execute(
                    text(f"""
                        select adapter_id, model_family, adapter_version, module_path,
                               is_baseline, executable, capabilities, data_requirements
                        from {SCHEMA}.kg_model_adapters where adapter_id = :id
                    """),
                    {"id": rec["adapter_id"]},
                ).mappings().first()
            if rec["policy_id"]:
                policy = connection.execute(
                    text(f"""
                        select policy_id, policy_version, dimensions, thresholds, penalties,
                               status, policy_sha256
                        from {SCHEMA}.kg_score_policies where policy_id = :id
                    """),
                    {"id": rec["policy_id"]},
                ).mappings().first()
            if rec["graph_version_id"]:
                graph_version = connection.execute(
                    text(f"""
                        select graph_version_id::text, document_set_hash, assertion_set_hash,
                               review_policy, status, integrity_report, published_at
                        from {SCHEMA}.kg_graph_versions where graph_version_id = cast(:id as uuid)
                    """),
                    {"id": rec["graph_version_id"]},
                ).mappings().first()
        return json_response(_jsonable({
            "score_type": SCORE_TYPE,
            "execution_status": EXECUTION_STATUS,
            "recommendation": dict(rec),
            "components": [dict(c) for c in components],
            "evidence_spans": [dict(e) for e in evidence_spans],
            "bundle": dict(bundle) if bundle else None,
            "adapter": dict(adapter) if adapter else None,
            "policy": dict(policy) if policy else None,
            "graph_version": dict(graph_version) if graph_version else None,
            "audit_statement": "No local model execution, no forecast run, no forecast value, no backtest metric.",
        }), 200)

    @app.get("/api/v1/kg/openapi")
    def kg_r3c_openapi(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        from fastapi.openapi.utils import get_openapi

        kg_routes = [
            r
            for r in app.routes
            if str(getattr(r, "path", "")).startswith("/api/v1/kg")
            and getattr(getattr(r, "endpoint", None), "__module__", "") == __name__
        ]
        schema = get_openapi(title="epidemic kg_r3c API", version="r3c.v1", routes=kg_routes)
        return json_response(schema, 200)

    # ---------------------------------------------------------------- writes
    @app.post("/api/v1/kg/import-runs")
    def kg_r3c_create_import_run(
        request: Request,
        payload: dict[str, Any] = Body(...),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        idem = idempotency_key
        endpoint = "/api/v1/kg/import-runs"
        import_key = str(payload.get("import_key") or "").strip()
        archive_path = str(payload.get("archive_path") or "").strip()
        archive_sha256 = str(payload.get("archive_sha256") or "").strip()
        if not import_key or not archive_path or not archive_sha256:
            return json_response({"detail": "INVALID_REQUEST"}, 422)
        request_hash = _canonical_hash(payload)
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            import_run_id = str(uuid.uuid4())
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {SCHEMA}.kg_import_runs
                        (import_run_id, import_key, archive_path, archive_sha256, status, actor)
                        VALUES (%s,%s,%s,%s,'OPEN',%s)
                        ON CONFLICT (import_key) DO NOTHING
                        RETURNING import_run_id, status""",
                    (import_run_id, import_key, archive_path, archive_sha256, AUDIT_ACTOR),
                )
                row = cur.fetchone()
                created = row is not None
                if not created:
                    cur.execute(
                        f"SELECT import_run_id, status FROM {SCHEMA}.kg_import_runs WHERE import_key=%s",
                        (import_key,),
                    )
                    row = cur.fetchone()
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 200)
        return json_response({
            "import_run_id": str(row["import_run_id"]),
            "import_key": import_key,
            "status": row["status"],
            "created": created,
            "audit_id": audit_id,
        }, 200)

    @app.post("/api/v1/kg/import-runs/{import_run_id}/extract")
    def kg_r3c_request_extraction(
        request: Request,
        import_run_id: str,
        payload: dict[str, Any] | None = Body(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(import_run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        idem = idempotency_key
        endpoint = "/api/v1/kg/import-runs/{id}/extract"
        request_hash = _canonical_hash({"import_run_id": import_run_id, "body": payload or {}})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE {SCHEMA}.kg_import_runs SET status='EXTRACT_REQUESTED'
                        WHERE import_run_id=%s AND status='OPEN'
                        RETURNING import_run_id, status""",
                    (import_run_id,),
                )
                row = cur.fetchone()
                transitioned = row is not None
                if not transitioned:
                    cur.execute(
                        f"SELECT import_run_id, status FROM {SCHEMA}.kg_import_runs WHERE import_run_id=%s",
                        (import_run_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return json_response({"detail": "IMPORT_RUN_NOT_FOUND"}, 404)
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 202)
        return json_response({
            "import_run_id": str(row["import_run_id"]),
            "status": row["status"],
            "extraction": "REQUESTED_OFFLINE_PIPELINE",
            "transitioned": transitioned,
            "audit_id": audit_id,
        }, 202)

    @app.post("/api/v1/kg/review-items/{review_item_id}/decisions")
    def kg_r3c_review_decision(
        request: Request,
        review_item_id: str,
        payload: dict[str, Any] = Body(...),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(review_item_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        decision = str(payload.get("decision") or "").strip()
        actor = str(payload.get("actor") or "").strip()
        rationale = payload.get("rationale")
        if not decision or not actor:
            return json_response({"detail": "INVALID_REQUEST"}, 422)
        idem = idempotency_key
        endpoint = "/api/v1/kg/review-items/{id}/decisions"
        request_hash = _canonical_hash({"review_item_id": review_item_id, "body": payload})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            decision_id = str(uuid.uuid4())
            new_status = decision.upper()
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT review_item_id, status FROM {SCHEMA}.kg_review_items WHERE review_item_id=%s",
                    (review_item_id,),
                )
                item = cur.fetchone()
                if not item:
                    return json_response({"detail": "REVIEW_ITEM_NOT_FOUND"}, 404)
                cur.execute(
                    f"""INSERT INTO {SCHEMA}.kg_review_decisions
                        (decision_id, review_item_id, decision, actor, rationale)
                        VALUES (%s,%s,%s,%s,%s)""",
                    (decision_id, review_item_id, decision, actor, rationale),
                )
                cur.execute(
                    f"UPDATE {SCHEMA}.kg_review_items SET status=%s WHERE review_item_id=%s",
                    (new_status, review_item_id),
                )
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 200)
        return json_response({
            "decision_id": decision_id,
            "review_item_id": review_item_id,
            "decision": decision,
            "item_status": new_status,
            "audit_id": audit_id,
        }, 200)

    @app.post("/api/v1/kg/data-profile-runs")
    def kg_r3c_data_profile_run(
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        idem = idempotency_key
        endpoint = "/api/v1/kg/data-profile-runs"
        request_hash = _canonical_hash(payload or {})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            from epidemic_core.kg_r3c.profiles import snapshot_and_profile

            results = snapshot_and_profile(conn)
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 200)
        return json_response(_jsonable({
            "profiles": [
                {
                    "series": list(r["series"]),
                    "profile_id": r["profile_id"],
                    "snapshot_id": r["snapshot_id"],
                    "profile_sha256": r["profile_sha256"],
                }
                for r in results
            ],
            "profile_count": len(results),
            "idempotent_by": "profile_sha256",
            "audit_id": audit_id,
        }), 200)

    @app.post("/api/v1/kg/recommendation-runs")
    def kg_r3c_recommendation_run(
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        idem = idempotency_key
        endpoint = "/api/v1/kg/recommendation-runs"
        request_hash = _canonical_hash(payload or {})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            with conn.cursor() as cur:
                cur.execute(
                    f"""select run_id, graph_version_id, policy_id, profile_set_hash, score_type,
                               status, actually_executed, forecast_run_id, execution_status, created_at
                        from {SCHEMA}.kg_recommendation_runs
                        order by created_at desc limit 1"""
                )
                row = cur.fetchone()
            if not row:
                _audit_record(conn, endpoint, "POST", idem, request_hash, 409)
                return json_response({"detail": "RECOMPUTE_REQUIRES_OFFLINE_PIPELINE"}, 409)
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 200)
        return json_response(_jsonable({
            "run": dict(row),
            "idempotent": True,
            "recomputed_in_request": False,
            "audit_id": audit_id,
        }), 200)

    @app.post("/api/v1/kg/r3c/recommendations/{recommendation_id}/execution-contracts")
    def kg_r3c_execution_contract(
        request: Request,
        recommendation_id: str,
        payload: dict[str, Any] | None = Body(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(recommendation_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        idem = idempotency_key
        endpoint = "/api/v1/kg/r3c/recommendations/{id}/execution-contracts"
        request_hash = _canonical_hash({"recommendation_id": recommendation_id, "body": payload or {}})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            from epidemic_core.kg_r3c.dbutil import sha256_json, stable_uuid

            with conn.cursor() as cur:
                cur.execute(
                    f"""select r.recommendation_id, r.run_id, r.profile_id, r.adapter_id, r.bundle_id,
                               s.country, s.disease, s.indicator, s.frequency, s.snapshot_sha256,
                               p.profile_sha256, p.profiler_version,
                               b.bundle_sha256, b.bundle_version,
                               a.adapter_version, a.model_family,
                               run.graph_version_id, run.policy_id, run.profile_set_hash
                        from {SCHEMA}.kg_recommendations r
                        join {SCHEMA}.kg_data_profiles p on p.profile_id = r.profile_id
                        join {SCHEMA}.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
                        join {SCHEMA}.kg_recommendation_runs run on run.run_id = r.run_id
                        left join {SCHEMA}.kg_parameter_bundles b on b.bundle_id = r.bundle_id
                        left join {SCHEMA}.kg_model_adapters a on a.adapter_id = r.adapter_id
                        where r.recommendation_id = %s""",
                    (recommendation_id,),
                )
                rec = cur.fetchone()
                if not rec:
                    return json_response({"detail": "RECOMMENDATION_NOT_FOUND"}, 404)
                cur.execute(
                    f"select task_sha256, horizons, target_semantics, leakage_boundary from {SCHEMA}.kg_forecast_tasks where profile_id=%s",
                    (str(rec["profile_id"]),),
                )
                task = cur.fetchone()
                cur.execute(
                    f"select policy_sha256, policy_version from {SCHEMA}.kg_score_policies where policy_id=%s",
                    (rec["policy_id"],),
                )
                policy = cur.fetchone()
                cur.execute(
                    f"select document_set_hash, assertion_set_hash from {SCHEMA}.kg_graph_versions where graph_version_id=%s",
                    (str(rec["graph_version_id"]),),
                )
                graph = cur.fetchone()
            contract = {
                "contract_version": "v1",
                "recommendation_id": recommendation_id,
                "score_type": SCORE_TYPE,
                "execution_status": EXECUTION_STATUS,
                "target": {
                    "country": rec["country"],
                    "disease": rec["disease"],
                    "indicator": rec["indicator"],
                    "frequency": rec["frequency"],
                },
                "horizons": list(task["horizons"]) if task and task.get("horizons") else HORIZONS,
                "target_semantics": task["target_semantics"] if task else None,
                "leakage_boundary": task["leakage_boundary"] if task else None,
                "bindings": {
                    "snapshot_sha256": rec["snapshot_sha256"],
                    "profile_sha256": rec["profile_sha256"],
                    "profiler_version": rec["profiler_version"],
                    "task_sha256": task["task_sha256"] if task else None,
                    "graph_version_id": str(rec["graph_version_id"]),
                    "document_set_hash": graph["document_set_hash"] if graph else None,
                    "assertion_set_hash": graph["assertion_set_hash"] if graph else None,
                    "policy_id": rec["policy_id"],
                    "policy_version": policy["policy_version"] if policy else None,
                    "policy_sha256": policy["policy_sha256"] if policy else None,
                    "adapter_id": rec["adapter_id"],
                    "adapter_version": rec["adapter_version"],
                    "bundle_sha256": rec["bundle_sha256"],
                    "profile_set_hash": rec["profile_set_hash"],
                },
                "backtest_design": {
                    "type": "rolling_origin",
                    "executor": "offline_pipeline_only",
                    "local_execution_in_request": False,
                },
                "probabilistic_interval_requirement": {"required": True, "levels": [0.8, 0.95]},
                "artifact_contract": {
                    "producer": "offline_pipeline",
                    "immutable": True,
                    "requires_human_review": True,
                },
                "non_execution_assertions": {
                    "actually_executed": False,
                    "fit_called": False,
                    "predict_called": False,
                },
            }
            contract_sha256 = sha256_json(contract)
            contract_id = stable_uuid("contract", contract_sha256)
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {SCHEMA}.kg_execution_contracts
                        (contract_id, recommendation_id, contract_version, contract, contract_sha256, status)
                        VALUES (%s,%s,'v1',%s::jsonb,%s,%s)
                        ON CONFLICT (contract_sha256) DO NOTHING
                        RETURNING contract_id, status""",
                    (contract_id, recommendation_id, json.dumps(contract, ensure_ascii=False), contract_sha256, EXECUTION_STATUS),
                )
                row = cur.fetchone()
                created = row is not None
                if not created:
                    cur.execute(
                        f"SELECT contract_id, status FROM {SCHEMA}.kg_execution_contracts WHERE contract_sha256=%s",
                        (contract_sha256,),
                    )
                    row = cur.fetchone()
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 200)
        return json_response({
            "contract_id": str(row["contract_id"]),
            "contract_sha256": contract_sha256,
            "status": row["status"],
            "created": created,
            "contract": contract,
            "audit_id": audit_id,
        }, 200)

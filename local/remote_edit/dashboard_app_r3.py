
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine, text

from .loader import load_graph
from .r4_shadow import ForecastOrchestrator, ModelRegistry
from .service import disease_graph_response, full_graph_response, guidance_response, metadata_response


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__str__") and value.__class__.__module__ == "decimal":
        return float(value)
    return value


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

KG_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>&#39044;&#27979;&#27169;&#22411;&#30693;&#35782;&#22270;&#35889; / Forecast Model Knowledge Graph</title>
  <link rel="stylesheet" href="/static/forecast-model-kg.css">
</head>
<body class="kg-page">
  <header>
    <div>
      <h1>&#39044;&#27979;&#27169;&#22411;&#30693;&#35782;&#22270;&#35889; / Forecast Model Knowledge Graph</h1>
      <p>&#25353;&#30149;&#31181;&#12289;&#20256;&#25773;&#26426;&#21046;&#12289;&#25968;&#25454;&#23494;&#24230;&#12289;&#39044;&#27979;&#30446;&#26631;&#12289;horizon &#21644;&#21327;&#21464;&#37327;&#26465;&#20214;&#32473;&#20986;&#27169;&#22411;&#20505;&#36873;&#12290;This page is guidance only: no training, no prediction run, no promotion.</p>
    </div>
    <nav>
      <a href="/">Dashboard</a>
      <a href="/predictions">Predictions</a>
      <a href="/fasttrack-forecast">Fasttrack</a>
    </nav>
    <form method="post" action="/logout"><button type="submit">Logout</button></form>
  </header>
  <main>
    <section class="kg-warning">
      <strong>&#38480;&#21046;&#35828;&#26126;</strong>
      &#26412;&#39029;&#19981;&#20195;&#26367; rolling-origin backtest&#12289;baseline guard &#25110;&#20154;&#24037;&#23457;&#26680;&#12290;&#27169;&#22411;&#36873;&#25321;&#24517;&#39035;&#30001;&#23454;&#38469;&#22238;&#27979;&#39564;&#35777;&#20915;&#23450;&#12290;
    </section>
    <section class="kg-controls" aria-label="Model guidance controls">
      <label>&#30149;&#31181; / Disease
        <input id="kgDisease" list="kgDiseases" value="Hantavirus">
        <datalist id="kgDiseases">
          <option>COVID-19</option><option>Influenza</option><option>Dengue</option><option>Ebola</option>
          <option>Puumala</option><option>Legionellosis</option><option>Nipah</option>
        </datalist>
      </label>
      <label>&#22269;&#23478;&#25110;&#22320;&#21306; / Country or region
        <input id="kgCountry" value="FIN" placeholder="United States / FIN / AUT / BGD">
      </label>
      <label>&#30446;&#26631; / Target<input id="kgTarget" value="puumala_virus_reports"></label>
      <label>&#39057;&#29575; / Frequency<input id="kgFrequency" value="week"></label>
      <label>Horizon weeks<input id="kgHorizon" type="number" min="1" max="52" value="4"></label>
      <label>演示画像 / Demo profile
        <select id="kgDemoProfile"><option value="">Use current database</option><option value="formal_sufficient">Data sufficient / FORMAL demo</option></select>
      </label>
      <button id="kgRun" type="button">&#29983;&#25104;&#24314;&#35758; / Generate guidance</button>
    </section>
    <section id="kgStatus" class="kg-status" aria-live="polite">Loading guidance...</section>
    <section id="kgDirectAnswer" class="kg-direct-answer" aria-live="polite"><h2>直接结论 / Direct answer</h2><p>Loading guidance...</p></section>
    <section class="kg-grid">
      <article><h2>1. Recommended model pool / &#25512;&#33616;&#27169;&#22411;&#27744;</h2><div id="kgModels"></div></article>
      <article><h2>2. Why / &#25512;&#33616;&#29702;&#30001;</h2><div id="kgReasons"></div></article>
      <article><h2>3. Validation requirements / &#39564;&#35777;&#35201;&#27714;</h2><div id="kgValidation"></div></article>
      <article><h2>4. Graph and table / &#22270;&#35889;&#19982;&#34920;&#26684;</h2><svg id="kgGraph" role="img" aria-label="Forecast model knowledge graph"></svg><div id="kgTableFallback"></div></article>
    </section>
    <section class="kg-orchestration">
      <h2>真实 DEMO_SHADOW 预测编排 / Real orchestration trace</h2>
      <p class="kg-muted">默认使用非 CDC 的 FIN / hantavirus / week / puumala_virus_reports。该演示只创建或读取 DEMO_SHADOW：不发布、不 promotion、不更新 active pointer。</p>
      <div class="kg-controls kg-controls-compact" aria-label="orchestration controls">
        <label>Country<input id="ux3Country" value="FIN"></label>
        <label>Disease<input id="ux3Disease" value="hantavirus"></label>
        <label>Metric<input id="ux3Metric" value="puumala_virus_reports"></label>
        <label>Cadence<input id="ux3Cadence" value="week"></label>
        <button id="ux3Run" type="button">运行/读取 DEMO_SHADOW</button>
      </div>
      <div id="ux3Status" class="kg-status">尚未运行 UX3 编排。</div>
      <div class="kg-grid">
        <article><h3>数据画像</h3><div id="ux3Profile"></div></article>
        <article><h3>候选模型与准入/拒绝</h3><div id="ux3Candidates"></div></article>
        <article><h3>回测、baseline guard 与选择原因</h3><div id="ux3Metrics"></div></article>
        <article><h3>最终 shadow 预测与区间</h3><div id="ux3Forecast"></div></article>
      </div>
    </section>
    <section class="kg-notes">
      <h2>&#30149;&#31181;&#36793;&#30028;&#25552;&#37266; / Disease boundary reminders</h2>
      <ul>
        <li>Finland Puumala guidance must not be generalized to all Hantavirus settings without ecological evidence.</li>
        <li>Legionella guidance requires a concrete country; Europe is not treated as a pseudo-country.</li>
        <li>Nipah guidance treats unreported periods as unknown sparse events; it must not fabricate zero-filled weekly series.</li>
      </ul>
    </section>
  </main>
  <script src="/static/forecast-model-kg.js" defer></script>
</body>
</html>"""


def install_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    json_response: Callable[[Any, int], Response],
) -> None:
    static_dir = Path(__file__).resolve().parents[1] / "dashboard" / "static"

    @app.get("/static/forecast-model-kg.css")
    def forecast_model_kg_css() -> FileResponse:
        return FileResponse(static_dir / "forecast-model-kg.css", media_type="text/css")

    @app.get("/static/forecast-model-kg.js")
    def forecast_model_kg_js() -> FileResponse:
        return FileResponse(static_dir / "forecast-model-kg.js", media_type="application/javascript")

    @app.get("/forecast-model-knowledge-graph")
    def forecast_model_kg_page(request: Request) -> Response:
        return HTMLResponse(KG_HTML) if authorized(request) else RedirectResponse("/login", 303)

    @app.get("/api/v1/forecast-model-knowledge-graph")
    def forecast_model_kg_api(request: Request, full: bool = False) -> Response:
        if not authorized(request):
            return unauthorized()
        try:
            return json_response(full_graph_response() if full else metadata_response(), 200)
        except Exception:
            return json_response({"detail": "FORECAST_MODEL_KG_LOAD_FAILED"}, 503)

    @app.get("/api/v1/forecast-model-knowledge-graph/{disease_id}")
    def forecast_model_kg_disease_api(request: Request, disease_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        try:
            return json_response(disease_graph_response(disease_id), 200)
        except ValueError:
            return json_response({"detail": "UNSUPPORTED_DISEASE"}, 404)
        except Exception:
            return json_response({"detail": "FORECAST_MODEL_KG_LOAD_FAILED"}, 503)

    @app.get("/api/v1/forecast-model-guidance")
    def forecast_model_guidance_api(
        request: Request,
        disease: str,
        country_or_region: str | None = None,
        target: str | None = "cases_new",
        horizon: int | None = 4,
        frequency: str | None = "week",
        demo_profile: str | None = None,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if horizon is not None and not 1 <= horizon <= 104:
            return json_response({"detail": "INVALID_HORIZON"}, 422)
        try:
            explicit_profile = None
            if demo_profile:
                if demo_profile != "formal_sufficient":
                    return json_response({"detail": "UNSUPPORTED_DEMO_PROFILE"}, 422)
                explicit_profile = {
                    "disease_id": None,
                    "country_or_region": country_or_region,
                    "forecast_target": target or "cases_new",
                    "horizon": horizon,
                    "time_granularity": "weekly",
                    "history_weeks_normalized": 156,
                    "period_count": 156,
                    "region_count": 8,
                    "nonzero_period_count": 64,
                    "zero_ratio": 0.2,
                    "is_event_series": False,
                    "is_continuous_surveillance": True,
                    "data_quality_status": "PASS",
                    "source_ids": ("demo-formal-sufficient-profile",),
                    "metric_fallback_used": False,
                }
            return json_response(guidance_response(engine, disease, country_or_region, target, horizon, explicit_profile=explicit_profile, frequency=frequency), 200)
        except ValueError as exc:
            code = str(exc) or "INVALID_REQUEST"
            return json_response({"detail": code}, 404 if code == "UNSUPPORTED_DISEASE" else 422)
        except Exception:
            return json_response({"detail": "FORECAST_MODEL_GUIDANCE_FAILED"}, 503)

    @app.get("/api/v1/forecast-model-kg/r4/model-registry")
    def forecast_model_kg_r4_model_registry(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        return json_response({"registry_version": "r4_shadow_v1", "models": ModelRegistry().executable()}, 200)

    @app.get("/api/v1/forecast-model-kg/r4/pending-candidates")
    def forecast_model_kg_r4_pending_candidates(request: Request, limit: int = 50) -> Response:
        if not authorized(request):
            return unauthorized()
        limit = max(1, min(int(limit), 200))
        with engine.connect() as connection:
            rows = connection.execute(
                text("""
                select candidate_id::text, claim_type, disease_text, model_text, parameter_name,
                       parameter_value, locator_status, physical_page, chunk_index, status,
                       recommendation_eligible, review_ready
                from epidemic_kg.kg_pending_claim_candidates
                where candidate_generation='R4_STRUCTURED'
                order by created_at desc, candidate_id
                limit :limit
                """),
                {"limit": limit},
            ).mappings().all()
        return json_response({"items": [dict(row) for row in rows]}, 200)

    @app.get("/api/v1/forecast-model-kg/r4/shadow-runs")
    def forecast_model_kg_r4_shadow_runs(request: Request, limit: int = 50) -> Response:
        if not authorized(request):
            return unauthorized()
        limit = max(1, min(int(limit), 200))
        with engine.connect() as connection:
            rows = connection.execute(
                text("""
                select run_id::text, mode, status, disease_slug, country_alpha3, metric_code,
                       cadence, data_through, model_name, fallback_reason, promotion_eligible,
                       active_pointer_changed, trusted_rows_changed, profile, selection_trace
                from epidemic_kg.kg_forecast_shadow_runs
                where idempotency_key like 'r4:%'
                order by created_at desc
                limit :limit
                """),
                {"limit": limit},
            ).mappings().all()
        return json_response(_jsonable({"items": [dict(row) for row in rows]}), 200)

    @app.get("/api/v1/forecast-model-kg/ux3/graph")
    def forecast_model_kg_ux3_graph(
        request: Request,
        search: str | None = "hantavirus",
        layer: str | None = "all",
        max_nodes: int = 80,
        max_edges: int = 120,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        max_nodes = max(5, min(int(max_nodes), 150))
        max_edges = max(0, min(int(max_edges), 300))
        layer_key = (layer or "all").strip().lower()
        if layer_key not in {"all", "forecast_guidance", "literature_evidence", "selection_trace"}:
            return json_response({"detail": "UNKNOWN_GRAPH_LAYER"}, 422)
        search_text = (search or "").strip().lower()
        q = f"%{search_text}%"
        api_nodes: list[dict[str, Any]] = []
        api_edges: list[dict[str, Any]] = []

        def matches_text(*values: Any) -> bool:
            if not search_text:
                return True
            return any(search_text in str(v or "").lower() for v in values)

        def add_node(node: dict[str, Any]) -> None:
            if len(api_nodes) < max_nodes and node["id"] not in {n["id"] for n in api_nodes}:
                api_nodes.append(node)

        def add_edge(edge: dict[str, Any]) -> None:
            if len(api_edges) < max_edges and edge["id"] not in {e["id"] for e in api_edges}:
                api_edges.append(edge)

        with engine.connect() as connection:
            counts: dict[str, int] = {
                str(k): int(v)
                for k, v in connection.execute(
                    text(
                        """
                        select node_type, count(*)::int
                        from epidemic_kg.kg_nodes
                        group by node_type
                        """
                    )
                ).all()
            }
            edge_counts: dict[str, int] = {
                str(k): int(v)
                for k, v in connection.execute(
                    text(
                        """
                        select predicate, count(*)::int
                        from epidemic_kg.kg_edges
                        group by predicate
                        """
                    )
                ).all()
            }
            if layer_key in {"all", "forecast_guidance"}:
                static_graph = load_graph()
                static_nodes = []
                for n in static_graph.get("nodes", []):
                    if matches_text(n.get("id"), n.get("label_en"), n.get("label_zh"), n.get("description_en"), n.get("description_zh"), " ".join(n.get("aliases", []))):
                        static_nodes.append(n)
                if not static_nodes and search_text:
                    matched_ids = {
                        e["source_id"]
                        for e in static_graph.get("edges", [])
                        if matches_text(e.get("id"), e.get("relation"), e.get("rationale_en"), e.get("rationale_zh"))
                    } | {
                        e["target_id"]
                        for e in static_graph.get("edges", [])
                        if matches_text(e.get("id"), e.get("relation"), e.get("rationale_en"), e.get("rationale_zh"))
                    }
                    static_nodes = [n for n in static_graph.get("nodes", []) if n.get("id") in matched_ids]
                selected_static_ids = {str(n["id"]) for n in static_nodes[:max_nodes]}
                for n in static_nodes:
                    add_node(
                        {
                            "id": f"fg:node:{n['id']}",
                            "raw_id": n["id"],
                            "source_layer": "forecast_guidance",
                            "type": n.get("type"),
                            "label": n.get("label_zh") or n.get("label_en") or n["id"],
                            "status": n.get("status", "ACTIVE"),
                            "summary": n.get("description_zh") or n.get("description_en"),
                            "source_revision": static_graph.get("metadata", {}).get("content_sha256"),
                            "metadata_allowlisted": {"version": n.get("version")},
                        }
                    )
                for e in static_graph.get("edges", []):
                    if e.get("source_id") in selected_static_ids and e.get("target_id") in selected_static_ids:
                        add_edge(
                            {
                                "id": f"fg:edge:{e['id']}",
                                "source_id": f"fg:node:{e['source_id']}",
                                "target_id": f"fg:node:{e['target_id']}",
                                "relation_type": e.get("relation"),
                                "source_layer": "forecast_guidance",
                                "status": e.get("priority") or "ACTIVE",
                                "claim_id_optional": None,
                                "evidence_locator_optional": e.get("evidence_ids", []),
                                "provenance": "reference/forecast_model_knowledge_graph.v1.json",
                            }
                        )

            if layer_key in {"all", "literature_evidence"}:
                nodes = connection.execute(
                    text(
                        """
                        select node_id::text as raw_node_id, canonical_key, node_type, display_name,
                               status, review_status, quarantine, evidence_eligible,
                               coalesce(properties, '{}'::jsonb) as properties
                        from epidemic_kg.kg_nodes
                        where lower(coalesce(display_name,'')) like :q
                           or lower(coalesce(canonical_key,'')) like :q
                           or (:q = '%%' and node_type in ('DISEASE','MODEL_OR_METHOD','WORK','CLAIM'))
                        order by
                          case node_type
                            when 'DISEASE' then 1
                            when 'MODEL_OR_METHOD' then 2
                            when 'CLAIM' then 3
                            when 'WORK' then 4
                            else 9
                          end,
                          display_name nulls last,
                          canonical_key
                        limit :max_nodes
                        """
                    ),
                    {"q": q if q != "%%" else "%%", "max_nodes": max_nodes},
                ).mappings().all()
                node_ids = [row["raw_node_id"] for row in nodes]
                for row in nodes:
                    add_node(
                        {
                            "id": f"kg:node:{row['raw_node_id']}",
                            "raw_id": row["raw_node_id"],
                            "source_layer": "literature_evidence",
                            "type": row["node_type"],
                            "label": row["display_name"] or row["canonical_key"] or row["raw_node_id"][:8],
                            "status": row["review_status"] or row["status"] or "UNKNOWN",
                            "summary": row["canonical_key"],
                            "source_revision": "live_epidemic_kg_db",
                            "metadata_allowlisted": {
                                "quarantine": row["quarantine"],
                                "evidence_eligible": row["evidence_eligible"],
                            },
                        }
                    )
                if node_ids:
                    edges = connection.execute(
                        text(
                            """
                            select edge_id::text, subject_node_id::text, predicate,
                                   object_node_id::text, relation_status, evidence_eligible,
                                   coalesce(properties, '{}'::jsonb) as properties
                            from epidemic_kg.kg_edges
                            where subject_node_id::text = any(:ids)
                              and object_node_id::text = any(:ids)
                            order by predicate, edge_id
                            limit :max_edges
                            """
                        ),
                        {"ids": node_ids, "max_edges": max_edges},
                    ).mappings().all()
                    allowed_node_ids = {str(n) for n in node_ids}
                    for row in edges:
                        if row["subject_node_id"] in allowed_node_ids and row["object_node_id"] in allowed_node_ids:
                            add_edge(
                                {
                                    "id": f"kg:edge:{row['edge_id']}",
                                    "source_id": f"kg:node:{row['subject_node_id']}",
                                    "target_id": f"kg:node:{row['object_node_id']}",
                                    "relation_type": row["predicate"],
                                    "source_layer": "literature_evidence",
                                    "status": row["relation_status"] or "UNKNOWN",
                                    "claim_id_optional": None,
                                    "evidence_locator_optional": None,
                                    "provenance": "live epidemic_kg.kg_edges",
                                }
                            )

            if layer_key in {"all", "selection_trace"}:
                runs = connection.execute(
                    text(
                        """
                        select run_id::text, disease_slug, country_alpha3, metric_code, cadence,
                               model_name, status, selection_trace
                        from epidemic_kg.kg_forecast_shadow_runs
                        where idempotency_key like 'ux3r1r2:%'
                          and (
                            lower(coalesce(disease_slug,'')) like :q or
                            lower(coalesce(country_alpha3,'')) like :q or
                            lower(coalesce(metric_code,'')) like :q or
                            lower(coalesce(model_name,'')) like :q or
                            :q = '%%'
                          )
                        order by created_at desc
                        limit 10
                        """
                    ),
                    {"q": q if q != "%%" else "%%"},
                ).mappings().all()
                for run in runs:
                    run_node = f"st:run:{run['run_id']}"
                    trace = run["selection_trace"] or {}
                    add_node(
                        {
                            "id": run_node,
                            "raw_id": run["run_id"],
                            "source_layer": "selection_trace",
                            "type": "SHADOW_RUN",
                            "label": f"{run['country_alpha3']} {run['disease_slug']} {run['model_name']}",
                            "status": run["status"] or "UNKNOWN",
                            "summary": f"{run['cadence']} {run['metric_code']}",
                            "source_revision": run["run_id"],
                            "metadata_allowlisted": {"mode": "DEMO_SHADOW"},
                        }
                    )
                    for cand in trace.get("candidate_models", [])[:20]:
                        cand_node = f"st:candidate:{cand.get('candidate_id')}"
                        add_node(
                            {
                                "id": cand_node,
                                "raw_id": cand.get("candidate_id"),
                                "source_layer": "selection_trace",
                                "type": "MODEL_CANDIDATE",
                                "label": cand.get("model_name"),
                                "status": cand.get("admission_status"),
                                "summary": cand.get("admission_or_rejection_reason") or cand.get("admission_reason"),
                                "source_revision": run["run_id"],
                                "metadata_allowlisted": {"actually_executed": cand.get("actually_executed"), "source_rule_ids": cand.get("source_rule_ids", [])},
                            }
                        )
                        add_edge(
                            {
                                "id": f"st:edge:{run['run_id']}:{cand.get('candidate_id')}",
                                "source_id": run_node,
                                "target_id": cand_node,
                                "relation_type": "EVALUATED_CANDIDATE",
                                "source_layer": "selection_trace",
                                "status": "EXECUTED" if cand.get("actually_executed") else "NOT_EXECUTED",
                                "claim_id_optional": cand.get("eligible_claim_ids", []),
                                "evidence_locator_optional": cand.get("source_rule_ids", []),
                                "provenance": "epidemic_kg.kg_forecast_shadow_runs.selection_trace",
                            }
                        )
        visible_node_ids = {n["id"] for n in api_nodes}
        api_edges = [e for e in api_edges if e["source_id"] in visible_node_ids and e["target_id"] in visible_node_ids]
        snapshot = {
            "schema_version": "ux3.graph.r2.v1",
            "source_layers": ["forecast_guidance", "literature_evidence", "selection_trace"],
            "nodes": api_nodes,
            "edges": api_edges,
            "counts": {"live_db_node_types": counts, "live_db_edge_predicates": edge_counts},
            "returned_nodes": len(api_nodes),
            "returned_edges": len(api_edges),
            "truncated": len(api_nodes) >= max_nodes or len(api_edges) >= max_edges,
            "filter_contract": {"search": search, "layer": layer_key, "max_nodes": max_nodes, "max_edges": max_edges, "layer_filter_effective": True},
        }
        snapshot["graph_hash"] = _hash_payload(snapshot)
        return json_response(_jsonable(snapshot), 200)

    @app.post("/api/v1/forecast-model-kg/ux3/demo-shadow-run")
    def forecast_model_kg_ux3_demo_shadow_run(
        request: Request,
        country: str = "FIN",
        disease: str = "hantavirus",
        cadence: str = "week",
        metric: str = "puumala_virus_reports",
        execution_id: str | None = None,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if disease.lower() in {"cdc_ili", "ilinet"} or metric.lower() in {"percent_weighted_ili", "weighted_ili"}:
            return json_response({"detail": "CDC_R4F_UNAUTHORIZED_FOR_UX3"}, 403)
        if cadence not in {"week", "month", "day"}:
            return json_response({"detail": "UNSAFE_CADENCE"}, 422)
        normalized_context = (
            country.strip().upper(),
            disease.strip().lower(),
            cadence.strip().lower(),
            metric.strip().lower(),
        )
        if normalized_context != ("FIN", "hantavirus", "week", "puumala_virus_reports"):
            return json_response({"detail": "UNKNOWN_UX3_R2_DEMO_DATASET"}, 403)
        raw = engine.raw_connection()
        try:
            driver_conn = getattr(raw, "driver_connection", raw)
            scope = "".join(ch for ch in (execution_id or "manual") if ch.isalnum() or ch in {"_", "-"}).strip("_-") or "manual"
            result = ForecastOrchestrator(driver_conn, token_prefix=f"ux3r1r2:{scope}").run(  # type: ignore[arg-type]
                normalized_context[0],
                normalized_context[1],
                normalized_context[2],
                normalized_context[3],
                mode="SHADOW",
            )
            driver_conn.commit()
        finally:
            raw.close()
        run_id = result["run_id"]
        with engine.connect() as connection:
            values = connection.execute(
                text(
                    """
                    select target_period_start, target_period_end, horizon,
                           yhat, lower_value, upper_value, actual_value,
                           is_observed, is_imputed, row_type
                    from epidemic_kg.kg_forecast_shadow_values
                    where run_id = :run_id
                    order by row_type, target_period_start, horizon
                    """
                ),
                {"run_id": run_id},
            ).mappings().all()
            run = connection.execute(
                text(
                    """
                    select run_id::text, mode, status, disease_slug, country_alpha3, metric_code,
                           cadence, data_through, model_name, fallback_reason, promotion_eligible,
                           active_pointer_changed, trusted_rows_changed, profile, selection_trace
                    from epidemic_kg.kg_forecast_shadow_runs
                    where run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            ).mappings().one()
        payload = {
            "schema_version": "ux3.demo_shadow.v1",
            "run": dict(run),
            "values": [dict(row) for row in values],
            "result": result,
            "non_promotion_assertions": {
                "mode": "SHADOW",
                "mode_equivalent": "DEMO_SHADOW",
                "promotion_eligible": False,
                "published": False,
                "active_pointer_delta": 0,
                "cdc_r4f_used": False,
            },
        }
        payload["response_hash"] = _hash_payload(payload)
        return json_response(_jsonable(payload), 200)

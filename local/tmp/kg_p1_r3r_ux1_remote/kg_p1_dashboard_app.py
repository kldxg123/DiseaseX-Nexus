from __future__ import annotations

import html
import json
from typing import Any, Callable

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import Engine, text

LEAK_MARKERS = ("/tmp/", "USER_HOME/", "password", "token", "cookie", "postgresql://")

def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items() if not any(m in str(k).lower() for m in ("password","token","cookie"))}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, str):
        out = v.replace("USER_HOME", "<server-path-redacted>").replace("/tmp", "<tmp-redacted>")
        for m in ("password","token","cookie","postgresql://"):
            out = out.replace(m, "<redacted>")
        return out
    return v

def _rows(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [{k: _clean(v) for k, v in dict(r).items()} for r in conn.execute(text(sql), params or {}).mappings().all()]

def _one(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = _rows(engine, sql, params)
    return rows[0] if rows else None

KG_EXPLORER_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KG P1 R2 · 模型推荐与证据溯源</title><link rel="stylesheet" href="/static/kg-p1-graph.css"></head><body class="kgx"><header><div><h1>知识图谱：数据 → 推荐 → 论文溯源</h1><p>按国家/病种/指标/频率选择真实数据序列，查看 provenance、局部关系图和可审计模型推荐。</p></div><nav><a href="/">Dashboard</a><a href="/forecast-model-knowledge-graph">模型KG</a><a href="/predictions">预测页</a></nav><form method="post" action="/logout"><button>退出</button></form></header><main><section class="card"><h2>1. 数据筛选与结果</h2><div class="filters"><label>国家/地区<input id="countrySearch" placeholder="输入中文/英文/ISO，如 France/FRA/BR"><select id="country"></select></label><label>病种<select id="disease"></select></label><label>指标/目标<select id="indicator"></select></label><label>频率<select id="frequency"></select></label><label>任务<select id="task"><option value="weekly_forecast">周度预测</option><option value="risk_screening">风险筛查</option></select></label><label>Horizon<select id="horizon"><option value="4">未来4周</option><option value="1">未来1周</option><option value="8">未来8周</option></select></label><button id="runQuery" type="button">查询并推荐</button></div><p id="summary" class="notice"></p><div id="resultTable"></div></section><section class="grid"><article class="card"><h2>2. 关系图与证据链</h2><svg id="graph" width="920" height="400"></svg><div id="graphMeta" class="muted"></div><h3>等价关系列表</h3><div id="edgeList"></div></article><article class="card"><h2>节点详情</h2><div id="detail">请选择一个结果或图节点。</div><h2>证据 / Provenance</h2><div id="evidence">等待选择。</div></article></section><section class="card"><h2>3. 模型推荐与推荐依据</h2><div id="recommendation">请先选择数据序列。</div></section><section class="card"><h2>高级实体搜索</h2><div class="filters"><input id="entityQ" placeholder="paper2 / AUC / XGBoost / COVID"><select id="entityType"><option value="">全部类型</option><option>WORK</option><option>DOCUMENT_VERSION</option><option>CLAIM</option><option>MODEL_OR_METHOD</option><option>DATA_SERIES</option></select><label><input type="checkbox" id="includeQ"> 显示隔离元数据</label><button id="entityBtn" type="button">搜索实体</button></div><div id="entityResults"></div></section><section class="card warning"><h2>Paper 1 隔离</h2><p>2024 arXiv v1 与 2025 Nature 正式版分离。Nature 正式版全文缺失，paper 1 默认不参与推荐，隔离证据不能冒充正式版证据。</p></section></main><script src="/static/kg-p1-graph.js" defer></script></body></html>"""

def install_routes(app: FastAPI, engine: Engine, authorized: Callable[[Request], bool], unauthorized: Callable[[], Response], json_response: Callable[[Any, int], Response]) -> None:
    def require(request: Request) -> Response | None:
        return None if authorized(request) else unauthorized()

    @app.get("/kg")
    @app.get("/kg/explorer")
    def kg_page(request: Request) -> Response:
        unauth = require(request)
        return unauth if unauth else HTMLResponse(KG_EXPLORER_HTML)

    @app.get("/static/kg-p1-graph.css")
    def kg_css() -> Response:
        return Response(KG_CSS, media_type="text/css")

    @app.get("/static/kg-p1-graph.js")
    def kg_js() -> Response:
        return Response(KG_JS, media_type="application/javascript")

    @app.get("/api/v1/kg/filter-options")
    def filter_options(request: Request, country: str | None = None, disease: str | None = None, indicator: str | None = None, frequency: str | None = None) -> Response:
        unauth = require(request)
        if unauth: return unauth
        base = "from epidemic_kg.kg_nodes where node_type='DATA_SERIES' and quarantine=false"
        clauses=[]; params={}
        if country: clauses.append("(properties->>'country_alpha2'=:country or properties->>'country_alpha3'=:country or properties->>'country_name' ilike :country_like)"); params.update(country=country, country_like=f"%{country}%")
        if disease: clauses.append("(properties->>'disease_slug'=:disease or properties->>'disease_name' ilike :disease_like)"); params.update(disease=disease, disease_like=f"%{disease}%")
        if indicator: clauses.append("properties->>'metric_code'=:indicator"); params["indicator"]=indicator
        if frequency: clauses.append("properties->>'frequency'=:frequency"); params["frequency"]=frequency
        where = (" and " + " and ".join(clauses)) if clauses else ""
        return json_response({
            "countries": _rows(engine, "select distinct properties->>'country_alpha2' alpha2, properties->>'country_alpha3' alpha3, properties->>'country_name' name from epidemic_kg.kg_nodes where node_type='DATA_SERIES' order by 1"),
            "diseases": _rows(engine, "select distinct properties->>'disease_slug' slug, properties->>'disease_name' name "+base+where+" order by 1", params),
            "indicators": _rows(engine, "select distinct properties->>'metric_code' code, properties->>'metric_name' name "+base+where+" order by 1", params),
            "frequencies": _rows(engine, "select distinct properties->>'frequency' frequency "+base+where+" order by 1", params),
        }, 200)

    @app.get("/api/v1/kg/datasets")
    def datasets(request: Request, country: str | None = None, disease: str | None = None, indicator: str | None = None, frequency: str | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)) -> Response:
        unauth = require(request)
        if unauth: return unauth
        clauses=["node_type='DATA_SERIES'","quarantine=false"]; params={"limit":limit,"offset":offset}
        if country: clauses.append("(properties->>'country_alpha2'=:country or properties->>'country_alpha3'=:country or properties->>'country_name' ilike :country_like)"); params.update(country=country, country_like=f"%{country}%")
        if disease: clauses.append("(properties->>'disease_slug'=:disease or properties->>'disease_name' ilike :disease_like)"); params.update(disease=disease, disease_like=f"%{disease}%")
        if indicator: clauses.append("properties->>'metric_code'=:indicator"); params["indicator"]=indicator
        if frequency: clauses.append("properties->>'frequency'=:frequency"); params["frequency"]=frequency
        where=" and ".join(clauses)
        total=_one(engine, f"select count(*)::int total from epidemic_kg.kg_nodes where {where}", params)["total"]
        rows=_rows(engine, f"""select node_id::text, display_name,
          properties->>'country_alpha2' country, properties->>'country_name' country_name,
          properties->>'disease_slug' disease, properties->>'disease_name' disease_name,
          properties->>'metric_code' indicator, properties->>'metric_name' indicator_name,
          properties->>'frequency' frequency, properties->>'start_date' start_date, properties->>'end_date' end_date,
          properties->>'latest_value' latest_value, properties->>'latest_date' latest_date,
          (properties->>'observation_count')::int observation_count, properties->>'source_name' source_name,
          properties->>'quality_status' quality_status, properties->>'review_trust_status' review_trust_status
          from epidemic_kg.kg_nodes where {where} order by country,disease,indicator,source_name limit :limit offset :offset""", params)
        reason = "OK" if rows else "NO_MATCHING_DATA_OR_FILTER_CONFLICT"
        return json_response({"items":rows,"total":total,"limit":limit,"offset":offset,"reason":reason},200)

    @app.get("/api/v1/kg/entities")
    def entities(request: Request, q: str | None = None, node_type: str | None = None, include_quarantined: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Response:
        unauth=require(request)
        if unauth: return unauth
        clauses=["(:inc or quarantine=false)"]; params={"inc":include_quarantined,"limit":limit,"offset":offset,"q":f"%{q or ''}%","node_type":node_type}
        if q: clauses.append("(display_name ilike :q or canonical_key ilike :q)")
        if node_type: clauses.append("node_type=:node_type")
        return json_response({"items":_rows(engine,"select node_id::text, node_type, display_name, status, trust_status, review_status, quarantine, evidence_eligible from epidemic_kg.kg_nodes where "+" and ".join(clauses)+" order by quarantine,node_type,display_name limit :limit offset :offset",params)},200)

    @app.get("/api/v1/kg/entities/{node_id}")
    def entity_detail(request: Request, node_id: str, include_quarantined: bool = False) -> Response:
        unauth=require(request)
        if unauth: return unauth
        row=_one(engine,"select node_id::text, canonical_key, node_type, display_name, status, trust_status, review_status, quarantine, evidence_eligible, properties from epidemic_kg.kg_nodes where node_id::text=:id and (:inc or quarantine=false)",{"id":node_id,"inc":include_quarantined})
        if not row: return json_response({"detail":"KG_ENTITY_NOT_FOUND"},404)
        return json_response({"entity":row,"readable":readable(row)},200)

    @app.get("/api/v1/kg/datasets/{node_id}/subgraph")
    def dataset_subgraph(request: Request, node_id: str, depth: int = Query(1, ge=1, le=2), limit: int = Query(60, ge=1, le=120)) -> Response:
        unauth=require(request)
        if unauth: return unauth
        center=_one(engine,"select node_id::text, node_type, display_name, properties from epidemic_kg.kg_nodes where node_id::text=:id and node_type='DATA_SERIES'",{"id":node_id})
        if not center: return json_response({"detail":"DATASET_NOT_FOUND"},404)
        edges=_rows(engine,"""select e.edge_id::text, e.predicate, e.relation_status, e.evidence_eligible, e.properties->>'why_exists' why_exists, e.properties->>'origin' origin,
          s.node_id::text subject_id, s.display_name subject_name, s.node_type subject_type,
          o.node_id::text object_id, o.display_name object_name, o.node_type object_type
          from epidemic_kg.kg_edges e join epidemic_kg.kg_nodes s on s.node_id=e.subject_node_id join epidemic_kg.kg_nodes o on o.node_id=e.object_node_id
          where e.subject_node_id::text=:id or e.object_node_id::text=:id order by e.predicate limit :limit""",{"id":node_id,"limit":limit})
        seen={center["node_id"]: {"node_id":center["node_id"],"node_type":center["node_type"],"display_name":center["display_name"]}}
        for e in edges:
            seen[e["subject_id"]]={"node_id":e["subject_id"],"node_type":e["subject_type"],"display_name":e["subject_name"]}
            seen[e["object_id"]]={"node_id":e["object_id"],"node_type":e["object_type"],"display_name":e["object_name"]}
        required={"ABOUT_GEOGRAPHY","ABOUT_DISEASE","PREDICTS_TARGET","USES_SOURCE"}
        got={e["predicate"] for e in edges}
        rev=_one(engine,"select version_num from epidemic.alembic_version limit 1") or {}
        return json_response({"root_node_id":node_id,"center":center,"nodes":list(seen.values()),"edges":edges,"missing_links":sorted(required-got),"node_count":len(seen),"edge_count":len(edges),"depth":depth,"limit":limit,"truncated":len(edges)>=limit,"database_revision":rev.get("version_num"),"graph_revision":"KG_P1_R3"},200)

    @app.get("/api/v1/kg/entities/{node_id}/provenance")
    def provenance(request: Request, node_id: str) -> Response:
        unauth=require(request)
        if unauth: return unauth
        ent=_one(engine,"select node_id::text,node_type,display_name,properties from epidemic_kg.kg_nodes where node_id::text=:id",{"id":node_id})
        if not ent: return json_response({"detail":"KG_ENTITY_NOT_FOUND"},404)
        if ent["node_type"]=="DATA_SERIES":
            p=ent.get("properties") or {}
            bind=_rows(engine,"select source_schema,source_table,source_pk_repr,trust_status from epidemic_kg.kg_source_bindings where node_id::text=:id order by source_schema,source_table limit 20",{"id":node_id})
            return json_response({"kind":"DATASET_PROVENANCE","summary":{
                "数据源":p.get("source_name"),"国家/地区":p.get("country_alpha2"),"病种":p.get("disease_slug"),"指标":p.get("metric_code"),"频率":p.get("frequency"),
                "时间覆盖":f"{p.get('start_date')} 至 {p.get('end_date')}","最新值":f"{p.get('latest_value')} @ {p.get('latest_date')}","观测点":p.get("observation_count"),"覆盖率":p.get("coverage_ratio"),
                "质量状态":p.get("quality_status"),"审核/信任状态":p.get("review_trust_status")},"bindings":bind,"gaps":["source contract/fetch/raw/normalization/snapshot layers are displayed when linked; missing links are not fabricated"]},200)
        return json_response({"kind":"NO_DATASET_PROVENANCE","reason":"NODE_TYPE_HAS_NO_DATASET_PROVENANCE"},200)

    @app.get("/api/v1/kg/claims/{claim_id}")
    def claim_detail(request: Request, claim_id: str, include_quarantined: bool = False) -> Response:
        unauth=require(request)
        if unauth: return unauth
        claims=_rows(engine,"select claim_id::text, canonical_key, claim_category, claim_status, subject_text, predicate_text, object_text, review_status, evidence_eligible, quarantine, metric_name, numeric_value, unit, comparator_or_baseline, direction, dataset_or_split, forecast_horizon, properties from epidemic_kg.kg_claims where claim_id::text=:id and (:inc or quarantine=false)",{"id":claim_id,"inc":include_quarantined})
        if not claims: return json_response({"detail":"KG_CLAIM_NOT_FOUND"},404)
        ev=_rows(engine,"select es.evidence_span_id::text, physical_page, printed_page_label, section_label, table_or_figure, array_to_string((regexp_split_to_array(es.raw_anchor,'\\s+'))[1:18],' ') anchor, es.anchor_start, es.anchor_end, es.page_text_sha256, es.anchor_sha256, es.evidence_eligible, es.quarantine from epidemic_kg.kg_claim_evidence ce join epidemic_kg.kg_evidence_spans es on es.evidence_span_id=ce.evidence_span_id where ce.claim_id::text=:id",{"id":claim_id})
        return json_response({"claim":claims[0],"evidence":ev,"reason": "NO_ELIGIBLE_EVIDENCE" if not ev else "EVIDENCE_LOCATED"},200)

    @app.get("/api/v1/kg/recommendations")
    def recommendations(request: Request, country: str | None = None, disease: str | None = None, indicator: str | None = None, frequency: str | None = None, horizon: int = Query(4, ge=1, le=12), task: str = "weekly_forecast", metric: str | None = None, series_id: str | None = None) -> Response:
        unauth=require(request)
        if unauth: return unauth
        clauses=["node_type='DATA_SERIES'","quarantine=false"]; params={"country":country,"disease":disease,"indicator":indicator,"frequency":frequency,"series_id":series_id}
        if series_id: clauses.append("node_id::text=:series_id")
        if country: clauses.append("(properties->>'country_alpha2'=:country or properties->>'country_alpha3'=:country or properties->>'country_name' ilike ('%'||:country||'%'))")
        if disease: clauses.append("(properties->>'disease_slug'=:disease or properties->>'disease_name' ilike ('%'||:disease||'%'))")
        if indicator: clauses.append("properties->>'metric_code'=:indicator")
        if frequency: clauses.append("properties->>'frequency'=:frequency")
        row=_one(engine,"select node_id::text, display_name, properties from epidemic_kg.kg_nodes where "+" and ".join(clauses)+" order by (properties->>'observation_count')::int desc limit 1",params)
        query={"country":country,"disease":disease,"indicator":indicator,"frequency":frequency,"horizon":horizon,"task":task,"series_id":series_id}
        versions={"recommendation_policy":"KG_P1_R3_v1","country_context_revision":"curated_local_v1","paper_evidence_revision":"kg_claims_non_quarantined_v1","data_revision":_one(engine,"select version_num from epidemic.alembic_version limit 1")}
        if not row:
            return json_response({"query":query,"current_readiness":{"status":"NO_MATCHING_DATA","can_train_now":False},"conditional_full_data_plan":{"preferred_model":"NOT_APPLICABLE_UNTIL_DATA_SERIES_EXISTS"},"paper1_participation":0,"warnings":["NO_MATCHING_DATA"] ,"versions":versions},200)
        p=row.get("properties") or {}; obs=int(p.get("observation_count") or 0); cov=float(p.get("coverage_ratio") or 0); nonzero=int(p.get("nonzero_count") or 0)
        is_cumulative=(p.get("metric_code") or "").endswith("cumulative")
        current_status = "FORMAL_READY_CANDIDATE" if (obs>=104 and cov>=0.85 and nonzero>=20 and not is_cumulative) else "PREVIEW_ONLY"
        if p.get("country_alpha2")=="BR":
            country_profile={"country":"Brazil","iso2":"BR","iso3":p.get("country_alpha3"),"spatial_scope":"当前 series 为国家级聚合；Brazil 全国气候与人口空间异质性强，不能简化为单一热带气候。","climate_population_source":"本地国家参考表 + KG_P1_R3_CURATED_CONTEXT；需要后续接入有版本的气象/人口协变量","context_limit":"没有市/州级气候协变量时，不声明气候驱动模型已可运行。"}
        elif p.get("country_alpha2")=="FR":
            country_profile={"country":"France","iso2":"FR","iso3":p.get("country_alpha3"),"spatial_scope":"当前 series 为国家级聚合；需区分本土与海外范围，不能混成单一气候背景。","climate_population_source":"本地国家参考表 + KG_P1_R3_CURATED_CONTEXT；COVID 累计口径优先处理修订/差分问题","context_limit":"累计病例不应直接作为平稳新增目标。"}
        else:
            country_profile={"country":p.get("country_name"),"iso2":p.get("country_alpha2"),"iso3":p.get("country_alpha3"),"spatial_scope":"从当前 DATA_SERIES 读取，未补充外部气候/人口事实。","context_limit":"NO_CURATED_COUNTRY_CONTEXT"}
        if p.get("disease_slug")=="dengue":
            conditional={"preferred_model":"XGBoost / gradient boosted trees with lag, seasonality, missing flags and optional climate covariates","why":"dengue 周度新增病例具有季节性、非线性和报告延迟；补齐多年至少104周且加入温度/降雨/湿度/人口或多地区面板后，树模型可作为首选候选。","search_space":{"lags":"1-8 weeks","seasonal_lags":"52 weeks when enough history","max_depth":"2-6 PROJECT_PROPOSED_SEARCH_SPACE","n_estimators":"100-800 PROJECT_PROPOSED_SEARCH_SPACE","learning_rate":"0.01-0.2 PROJECT_PROPOSED_SEARCH_SPACE"}}
            not_first=["graph neural network until multi-region panel and real spatial edges exist","deep sequence model until longer history and covariates exist"]
        elif p.get("disease_slug")=="covid-19" and is_cumulative:
            conditional={"preferred_model":"First derive audited weekly new cases, then compare seasonal naive / regularized GLM-GAM / gradient boosting","why":"cases_cumulative 是累计口径；必须先处理修订、差分、负增量和时间切分泄漏，再决定新增病例预测模型。","search_space":{"difference_rule":"audited cumulative-to-new conversion","lags":"1-8 weeks after conversion","regularization":"PROJECT_PROPOSED_SEARCH_SPACE","tree_depth":"2-4 only after target conversion"}}
            not_first=["direct XGBoost on cumulative target","deep model without leakage-safe target conversion"]
        else:
            conditional={"preferred_model":"seasonal naive + regularized GLM/GAM baseline first","why":"当前画像未命中专门病种规则；先建立可解释 baseline。","search_space":{"lags":"1-8","regularization":"library default then tune by rolling-origin"}}
            not_first=["deep or graph model as first choice"]
        paper_matches=_rows(engine,"select claim_id::text, claim_category, object_text, metric_name, coalesce(numeric_value,'NOT_REPORTED_OR_NOT_EXTRACTED') numeric_value, coalesce(unit,'NOT_REPORTED_OR_NOT_EXTRACTED') unit, coalesce(nullif(comparator_or_baseline,''),'NOT_REPORTED_OR_NOT_EXTRACTED') comparator, 'NEARBY_OR_GENERAL_EVIDENCE' evidence_role from epidemic_kg.kg_claims where quarantine=false and evidence_eligible=true and claim_status='DIRECTLY_REPORTED' and claim_category not in ('code_and_data_availability','REPRODUCIBILITY_OR_AVAILABILITY') order by claim_category limit 6")
        return json_response({"query":query,"data_profile":p,"country_region_profile":country_profile,"disease_source_profile":{"disease":p.get("disease_slug"),"source":p.get("source_name"),"frequency":p.get("frequency"),"target_semantics":"cumulative" if is_cumulative else "incident_or_count"},"current_readiness":{"status":current_status,"can_train_now":current_status=='FORMAL_READY_CANDIDATE',"reason":"当前数据不足或口径不适合正式训练" if current_status!='FORMAL_READY_CANDIDATE' else "仍需真实 rolling-origin backtest 才能发布"},"current_baselines":["last value baseline","seasonal naive baseline"],"conditional_full_data_plan":conditional,"not_recommended_first":not_first,"paper_matches":paper_matches,"hyperparameter_evidence":[{"kind":"PAPER_DIRECTLY_REPORTED","value":"NOT_REPORTED_OR_NOT_EXTRACTED"},{"kind":"PROJECT_PROPOSED_INITIAL_CONFIG","value":conditional["search_space"]},{"kind":"PROJECT_PROPOSED_SEARCH_SPACE","value":conditional["search_space"]}],"data_gap_plan":["补齐至少104周且覆盖率达到门槛","确认空间粒度和修订延迟","如使用气候/人口协变量，必须接入有来源和版本的数据"],"audit_rationale":[{"fact":"真实数据画像","source":"epidemic_kg.kg_nodes.properties","rule_id":"KG_P1_R3_PROFILE","rule_version":"v1","effect_on_recommendation":current_status,"evidence_role":"DATASET_DERIVED_PROFILE","eligibility":"eligible","limitation":"不代表已训练或已验证最优"},{"fact":"paper1 participation = 0","source":"quarantine policy","rule_id":"KG_P1_R3_PAPER1_EXCLUSION","rule_version":"v1","effect_on_recommendation":"exclude paper1","evidence_role":"LIMITATION","eligibility":"required","limitation":"paper1 Nature PDF missing"}],"warnings":["NO_DIRECT_PAPER_MATCH: 以下为邻近证据或项目规则，不构成当前国家/病种已验证最优模型","不启动训练、预测或 GPU"],"paper1_participation":0,"versions":versions},200)

def readable(row: dict[str, Any]) -> dict[str, Any]:
    p=row.get("properties") or {}; t=row.get("node_type")
    if t=="DATA_SERIES":
        return {"它是什么":"一条按业务键聚合的公共卫生时间序列，不是一条 observation","为什么出现":f"匹配 {p.get('country_alpha2')} / {p.get('disease_slug')} / {p.get('metric_code')} / {p.get('frequency')}","关键字段":{"国家":p.get("country_name"),"病种":p.get("disease_name"),"指标":p.get("metric_code"),"频率":p.get("frequency"),"来源":p.get("source_name")},"时间覆盖":f"{p.get('start_date')} 至 {p.get('end_date')}","观测点":p.get("observation_count"),"最新值":f"{p.get('latest_value')} @ {p.get('latest_date')}","证据完整性":"查看 Evidence/Provenance 面板"}
    return {"它是什么":row.get("node_type"),"名称":row.get("display_name"),"审核状态":row.get("review_status"),"是否隔离":row.get("quarantine"),"说明":"开发者 JSON 已默认隐藏；如需审计请看专用证据/Provenance API。"}

KG_CSS = """body.kgx{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#f5f7fb;color:#10213f}header{display:flex;justify-content:space-between;gap:1rem;background:#173f7a;color:white;padding:1rem 2rem}header a{color:#dcecff;margin-right:1rem}.card{background:white;border:1px solid #d8e2ef;border-radius:12px;margin:1rem 2rem;padding:1rem;box-shadow:0 2px 8px #0d2b5a10}.filters{display:flex;flex-wrap:wrap;gap:.8rem;align-items:end}.filters label{font-size:13px}.filters input,.filters select{display:block;min-width:160px;padding:.45rem;border:1px solid #aac0dd;border-radius:8px}button{background:#1c5aa6;color:white;border:0;border-radius:8px;padding:.55rem .9rem}.grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(320px,1fr);gap:1rem;margin:0 1rem}.notice{background:#fff8df;border:1px solid #ecd58b;border-radius:10px;padding:.7rem}.warning{border-color:#e3b341}table{width:100%;border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid #e5ebf5;padding:.45rem;text-align:left}.muted{color:#65758b}.pill{display:inline-block;background:#eaf2ff;color:#17437a;border-radius:999px;padding:.15rem .45rem;margin:.1rem}.edge{stroke:#557;stroke-width:1.5}.node{cursor:pointer}.dev{display:none}"""

KG_JS = r"""const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));async function j(u){const r=await fetch(u,{credentials:'same-origin'});if(!r.ok)throw new Error(await r.text());return r.json()}let selected=null;async function loadOptions(){const d=await j('/api/v1/kg/filter-options');$('country').innerHTML=d.countries.map(x=>`<option value="${esc(x.alpha2)}">${esc(x.alpha2)} · ${esc(x.alpha3||'')} · ${esc(x.name||'')}</option>`).join('');$('disease').innerHTML=d.diseases.map(x=>`<option value="${esc(x.slug)}">${esc(x.name||x.slug)}</option>`).join('');$('indicator').innerHTML=d.indicators.map(x=>`<option value="${esc(x.code)}">${esc(x.code)} · ${esc(x.name||'')}</option>`).join('');$('frequency').innerHTML=d.frequencies.map(x=>`<option value="${esc(x.frequency)}">${esc(x.frequency)}</option>`).join('');let br=[...$('country').options].find(o=>o.value==='BR');if(br)$('country').value='BR';let den=[...$('disease').options].find(o=>o.value==='dengue');if(den)$('disease').value='dengue';let cn=[...$('indicator').options].find(o=>o.value==='cases_new');if(cn)$('indicator').value='cases_new';let wk=[...$('frequency').options].find(o=>o.value==='week');if(wk)$('frequency').value='week'}function params(){let c=$('countrySearch').value.trim()||$('country').value;return new URLSearchParams({country:c,disease:$('disease').value,indicator:$('indicator').value,frequency:$('frequency').value,horizon:$('horizon').value,task:$('task').value})}async function query(){try{let p=params();let data=await j('/api/v1/kg/datasets?'+p);$('summary').textContent=`匹配 ${data.total} 条 series；当前显示 ${data.items.length} 条。`;$('resultTable').innerHTML='<table><thead><tr><th>国家</th><th>病种</th><th>指标</th><th>频率</th><th>开始</th><th>结束</th><th>最新值</th><th>点数</th><th>来源</th><th>质量/审核</th></tr></thead><tbody>'+data.items.map(x=>`<tr data-id="${x.node_id}"><td>${esc(x.country)}</td><td>${esc(x.disease)}</td><td>${esc(x.indicator)}</td><td>${esc(x.frequency)}</td><td>${esc(x.start_date)}</td><td>${esc(x.end_date)}</td><td>${esc(x.latest_value)} @ ${esc(x.latest_date)}</td><td>${esc(x.observation_count)}</td><td>${esc(x.source_name)}</td><td>${esc(x.quality_status)} / ${esc(x.review_trust_status)}</td></tr>`).join('')+'</tbody></table>';document.querySelectorAll('#resultTable tr[data-id]').forEach(tr=>tr.onclick=()=>selectDataset(tr.dataset.id));if(data.items[0]) await selectDataset(data.items[0].node_id);else{$('detail').textContent='空结果：无数据或筛选条件冲突。';$('evidence').textContent='NO_MATCHING_DATA_OR_FILTER_CONFLICT';}}catch(e){$('summary').textContent='错误：'+e.message}}async function selectDataset(id){selected=id;let [sg,detail,prov,rec]=await Promise.all([j(`/api/v1/kg/datasets/${id}/subgraph`),j(`/api/v1/kg/entities/${id}`),j(`/api/v1/kg/entities/${id}/provenance`),j('/api/v1/kg/recommendations?'+params())]);draw(sg);$('detail').innerHTML=card(detail.readable);$('evidence').innerHTML=provHTML(prov);$('recommendation').innerHTML=recHTML(rec)}function card(o){return '<dl>'+Object.entries(o).map(([k,v])=>`<dt><b>${esc(k)}</b></dt><dd>${typeof v==='object'?card(v):esc(v)}</dd>`).join('')+'</dl>'}function provHTML(p){return `<p><b>${esc(p.kind)}</b></p>${card(p.summary||{})}<h4>source bindings</h4><ul>${(p.bindings||[]).map(b=>`<li>${esc(b.source_schema)}.${esc(b.source_table)} · ${esc(b.trust_status)}</li>`).join('')||'<li>PROVENANCE_LINK_INCOMPLETE</li>'}</ul><p>${esc((p.gaps||[]).join('；'))}</p>`}function recHTML(r){return `<h3>${esc(r.primary_model||r.recommendation)}</h3><p><span class="pill">${esc(r.confidence)}</span> paper1 participation=${esc(r.paper1_participation)}</p><ol>${(r.rationale_chain||[]).map(x=>`<li><b>${esc(x.step)}：</b>${esc(x.result)}</li>`).join('')}</ol><h4>关联论文证据</h4><ul>${(r.evidence||[]).map(e=>`<li>${esc(e.claim_category)} · ${esc(e.object_text)} ${esc(e.numeric_value||'')} ${esc(e.unit||'')} comparator=${esc(e.comparator_or_baseline||'')}</li>`).join('')||'<li>CURATED_RULE_ONLY</li>'}</ul><p>${esc(r.transfer_warning||'')}</p>`}function draw(g){let svg=$('graph');svg.innerHTML='';let nodes=g.nodes||[],edges=g.edges||[];let pos={};nodes.forEach((n,i)=>{let angle=2*Math.PI*i/Math.max(1,nodes.length);let r=i?150:0;pos[n.node_id]={x:460+r*Math.cos(angle),y:200+r*Math.sin(angle)}});edges.forEach(e=>{let a=pos[e.subject_id],b=pos[e.object_id];if(!a||!b)return;svg.insertAdjacentHTML('beforeend',`<line class="edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"></line><text x="${(a.x+b.x)/2}" y="${(a.y+b.y)/2}" font-size="11">${esc(e.predicate)}</text>`)});nodes.forEach(n=>{let p=pos[n.node_id];svg.insertAdjacentHTML('beforeend',`<g class="node" data-id="${n.node_id}"><circle cx="${p.x}" cy="${p.y}" r="24" fill="${n.node_type==='DATA_SERIES'?'#1c5aa6':'#6b8e23'}"></circle><text x="${p.x}" y="${p.y+42}" text-anchor="middle" font-size="11">${esc(n.display_name)}</text></g>`)});$('edgeList').innerHTML='<table><thead><tr><th>起点</th><th>关系中文/内部类型</th><th>终点</th></tr></thead><tbody>'+edges.map(e=>`<tr><td>${esc(e.subject_name)}</td><td>${esc({'ABOUT_GEOGRAPHY':'关于国家/地区','ABOUT_DISEASE':'关于病种','PREDICTS_TARGET':'预测目标','USES_SOURCE':'使用数据源'}[e.predicate]||e.predicate)} / ${esc(e.predicate)}</td><td>${esc(e.object_name)}</td></tr>`).join('')+'</tbody></table>';$('graphMeta').textContent=`节点 ${g.node_count}，边 ${g.edge_count}，深度 ${g.depth}，上限 ${g.limit}，缺失关系 ${esc((g.missing_links||[]).join(','))}`;}async function entitySearch(){let q=encodeURIComponent($('entityQ').value),t=encodeURIComponent($('entityType').value),inc=$('includeQ').checked;let d=await j(`/api/v1/kg/entities?q=${q}&node_type=${t}&include_quarantined=${inc}`);$('entityResults').innerHTML='<table><thead><tr><th>类型</th><th>名称</th><th>状态</th><th>隔离</th></tr></thead><tbody>'+d.items.map(x=>`<tr><td>${esc(x.node_type)}</td><td>${esc(x.display_name)}</td><td>${esc(x.review_status)}</td><td>${x.quarantine?'是':'否'}</td></tr>`).join('')+'</tbody></table>'}$('runQuery').onclick=query;$('entityBtn').onclick=entitySearch;loadOptions().then(query).catch(e=>$('summary').textContent='初始化错误：'+e.message);"""

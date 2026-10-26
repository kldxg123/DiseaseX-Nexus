from __future__ import annotations

import html
import json
from typing import Any, Callable

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import Engine, text
from epidemic_core.forecast_model_kg.service import guidance_response as ux1_guidance_response

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
        target = metric or indicator or "cases_new"
        disease_name = disease or "dengue"
        try:
            result = ux1_guidance_response(engine, disease_name, country, target, horizon, explicit_profile=None, frequency=frequency or "week")
            result["kg_explorer_adapter"] = {
                "source": "forecast_model_kg.service.guidance_response",
                "contract": result.get("r3r_contract_version"),
                "task": task,
                "series_id_requested": series_id,
            }
            return json_response(result, 200)
        except ValueError as exc:
            return json_response({"detail": str(exc) or "INVALID_RECOMMENDATION_REQUEST"}, 422)
        except Exception:
            return json_response({"detail": "KG_RECOMMENDATION_GUIDANCE_FAILED"}, 503)

def readable(row: dict[str, Any]) -> dict[str, Any]:
    p=row.get("properties") or {}; t=row.get("node_type")
    if t=="DATA_SERIES":
        return {"它是什么":"一条按业务键聚合的公共卫生时间序列，不是一条 observation","为什么出现":f"匹配 {p.get('country_alpha2')} / {p.get('disease_slug')} / {p.get('metric_code')} / {p.get('frequency')}","关键字段":{"国家":p.get("country_name"),"病种":p.get("disease_name"),"指标":p.get("metric_code"),"频率":p.get("frequency"),"来源":p.get("source_name")},"时间覆盖":f"{p.get('start_date')} 至 {p.get('end_date')}","观测点":p.get("observation_count"),"最新值":f"{p.get('latest_value')} @ {p.get('latest_date')}","证据完整性":"查看 Evidence/Provenance 面板"}
    return {"它是什么":row.get("node_type"),"名称":row.get("display_name"),"审核状态":row.get("review_status"),"是否隔离":row.get("quarantine"),"说明":"开发者 JSON 已默认隐藏；如需审计请看专用证据/Provenance API。"}

KG_CSS = r"""*{box-sizing:border-box}
html,body{max-width:100%;overflow-x:hidden}
body.kgx{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#f5f7fb;color:#10213f;line-height:1.55}
header{display:flex;justify-content:space-between;gap:1rem;background:#173f7a;color:white;padding:1rem 2rem;flex-wrap:wrap;min-width:0}
header>*{min-width:0}header a{color:#dcecff;margin-right:1rem}
.card{background:white;border:1px solid #d8e2ef;border-radius:12px;margin:1rem 2rem;padding:1rem;box-shadow:0 2px 8px #0d2b5a10;min-width:0;max-width:100%;overflow:hidden}
.filters{display:flex;flex-wrap:wrap;gap:.8rem;align-items:end;min-width:0}.filters label{font-size:13px;min-width:0}
.filters input,.filters select{display:block;min-width:0;width:min(240px,100%);padding:.45rem;border:1px solid #aac0dd;border-radius:8px}
button{background:#1c5aa6;color:white;border:0;border-radius:8px;padding:.55rem .9rem}
.grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:1rem;margin:0 1rem;min-width:0;max-width:100%}.grid>*{min-width:0;max-width:100%}
.notice{background:#fff8df;border:1px solid #ecd58b;border-radius:10px;padding:.7rem}.warning{border-color:#e3b341}
.table-wrap{width:100%;max-width:100%;overflow-x:auto;border:1px solid #d8e2ef;border-radius:10px;margin:.5rem 0}
table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}td,th{border-bottom:1px solid #e5ebf5;padding:.45rem;text-align:left;vertical-align:top;white-space:normal;overflow-wrap:anywhere;word-break:break-word}
th{background:#eef4fb}.muted{color:#65758b}.pill{display:inline-block;background:#eaf2ff;color:#17437a;border-radius:999px;padding:.15rem .45rem;margin:.1rem;overflow-wrap:anywhere}
.edge{stroke:#557;stroke-width:1.5}.node{cursor:pointer}.dev{display:none}
#graph{display:block;width:100%;max-width:100%;height:auto;border:1px solid #d8e2ef;border-radius:10px;background:#fbfdff}
dl,dd,dt,p,li,h3,h4{overflow-wrap:anywhere;word-break:break-word}
.rec-readable{min-width:0;max-width:100%}.rec-card{border:1px solid #d8e2ef;border-radius:10px;background:#fbfdff;padding:.75rem;margin:.55rem 0;min-width:0;max-width:100%;overflow:hidden}
.rec-card h4{margin:.15rem 0}.rec-card p{margin:.3rem 0}
@media(max-width:900px){header{display:block;padding:1rem}.card{margin:1rem}.grid{grid-template-columns:1fr;margin:0}.filters{display:grid;grid-template-columns:1fr}.filters input,.filters select{width:100%}}
"""

KG_JS = r"""const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const modelNames={
  gradient_boosted_trees_xgboost_after_data_completion:'补齐数据后的梯度提升树 / XGBoost',
  regularized_negative_binomial_or_gam:'正则化负二项回归 / GAM',
  graph_neural_network:'图神经网络',
  deep_sequence_model:'深度序列模型',
  audited_cumulative_to_incident_then_baseline_glm_gam_or_boosting:'先审计累计转新增，再比较 baseline / GLM-GAM / 树模型',
  state_space_or_ensemble_after_conversion:'转换后的状态空间或集成模型',
  direct_model_on_cases_cumulative:'直接对累计病例建模',
  last_observed_value:'最后观测值基线',
  seasonal_naive:'季节性 naive 基线'
};
const statusNames={
  FORMAL_READY_CANDIDATE:'形式上具备建模候选资格（仍需回测）',
  BASELINE_ONLY:'当前只适合 baseline / 预览',
  PREVIEW_ONLY:'当前仅适合预览，暂不进入正式预测',
  DIRECT_MATCH_AVAILABLE:'存在直接匹配论文证据',
  NO_DIRECT_MATCH_NEARBY_AVAILABLE:'没有直接论文，但存在邻近论文证据',
  NO_DIRECT_PAPER_MATCH_WITH_NEARBY_EVIDENCE:'没有直接论文，但存在邻近论文证据',
  NO_ELIGIBLE_PAPER_AFTER_COMPLETED_SEARCH:'检索已完成，但无合格直接或邻近论文',
  PAPER_INDEX_NOT_READY:'论文索引尚未就绪',
  PAPER_RETRIEVAL_FAILED:'论文检索失败',
  true:'当前可执行',
  false:'当前暂不可执行'
};
const fieldNames={history_weeks:'历史长度',coverage:'时间覆盖率',recent_coverage:'近期覆盖率',nonzero_weeks:'非零观测周',covariates:'所需协变量',covariates_gap:'当前缺少协变量',history_weeks_gap:'历史长度差距',coverage_gap:'覆盖率差距',nonzero_weeks_gap:'非零周差距',audited_conversion:'累计转新增审计',leakage_safe_cutoff:'无泄漏时间切分',target_conversion:'目标口径转换',negative_revision_check:'负增量/修订检查',baseline_first:'先比较基线',horizon_weeks:'预测 horizon',frequency:'频率'};
async function j(u){const r=await fetch(u,{credentials:'same-origin'});const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'}));if(!r.ok)throw new Error(d.detail||d.message||`HTTP ${r.status}`);return d}
function human(s){return statusNames[s]||modelNames[s]||String(s??'').replaceAll('_',' ')}
function key(k){return fieldNames[k]||String(k||'').replaceAll('_',' ')}
function val(v,ctx){if(v===null||v===undefined||v==='')return ctx==='gap'?'当前无已识别差距':ctx==='minimum'?'暂无额外最低条件':'未提供';if(typeof v==='boolean')return v?'当前可执行':'当前暂不可执行';if(Array.isArray(v))return v.length?v.map(x=>fieldNames[x]||human(x)).join('、'):'未提供';if(typeof v==='object'){let e=Object.entries(v);return e.length?e.map(([k,v])=>`${key(k)}：${val(v,ctx)}`).join('；'):(ctx==='gap'?'当前无已识别差距':'暂无额外最低条件')}return human(v)}
function wrap(html){return `<div class="table-wrap">${html}</div>`}
function table(headers,rows){return wrap(`<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`)}
function pill(s){return `<span class="pill">${esc(human(s))}</span>`}
function modelName(m){if(!m)return'未提供';return m.display_name||modelNames[m.name]||m.name||'未提供'}
let selected=null;
async function loadOptions(){const d=await j('/api/v1/kg/filter-options');$('country').innerHTML=d.countries.map(x=>`<option value="${esc(x.alpha2)}">${esc(x.alpha2)} · ${esc(x.alpha3||'')} · ${esc(x.name||'')}</option>`).join('');$('disease').innerHTML=d.diseases.map(x=>`<option value="${esc(x.slug)}">${esc(x.name||x.slug)}</option>`).join('');$('indicator').innerHTML=d.indicators.map(x=>`<option value="${esc(x.code)}">${esc(x.code)} · ${esc(x.name||'')}</option>`).join('');$('frequency').innerHTML=d.frequencies.map(x=>`<option value="${esc(x.frequency)}">${esc(x.frequency)}</option>`).join('');let br=[...$('country').options].find(o=>o.value==='BR');if(br)$('country').value='BR';let den=[...$('disease').options].find(o=>o.value==='dengue');if(den)$('disease').value='dengue';let cn=[...$('indicator').options].find(o=>o.value==='cases_new');if(cn)$('indicator').value='cases_new';let wk=[...$('frequency').options].find(o=>o.value==='week');if(wk)$('frequency').value='week'}
function params(){let c=$('countrySearch').value.trim()||$('country').value;return new URLSearchParams({country:c,disease:$('disease').value,indicator:$('indicator').value,frequency:$('frequency').value,horizon:$('horizon').value,task:$('task').value})}
function guidanceParams(){let c=$('countrySearch').value.trim()||$('country').value;return new URLSearchParams({country_or_region:c,disease:$('disease').value,target:$('indicator').value,frequency:$('frequency').value,horizon:$('horizon').value})}
async function query(){try{let p=params();let data=await j('/api/v1/kg/datasets?'+p);$('summary').textContent=`匹配 ${data.total} 条 series；当前显示 ${data.items.length} 条。`; $('resultTable').innerHTML=table(['国家','病种','指标','频率','开始','结束','最新值','点数','来源','质量/审核'],data.items.map(x=>[esc(x.country),esc(x.disease),esc(x.indicator),esc(x.frequency),esc(x.start_date),esc(x.end_date),esc(`${x.latest_value} @ ${x.latest_date}`),esc(x.observation_count),esc(x.source_name),esc(`${x.quality_status} / ${x.review_trust_status}`)]));document.querySelectorAll('#resultTable tbody tr').forEach((tr,i)=>tr.onclick=()=>selectDataset(data.items[i].node_id));if(data.items[0])await selectDataset(data.items[0].node_id);else{$('detail').textContent='空结果：无数据或筛选条件冲突。';$('evidence').textContent='无匹配数据，推荐区不会留空。';$('recommendation').innerHTML='<p>没有匹配数据序列，因此不能生成模型推荐。</p>'}}catch(e){$('summary').textContent='错误：'+e.message}}
async function selectDataset(id){selected=id;let [sg,detail,prov,rec]=await Promise.all([j(`/api/v1/kg/datasets/${id}/subgraph`),j(`/api/v1/kg/entities/${id}`),j(`/api/v1/kg/entities/${id}/provenance`),j('/api/v1/forecast-model-guidance?'+guidanceParams())]);draw(sg);$('detail').innerHTML=card(detail.readable);$('evidence').innerHTML=provHTML(prov);$('recommendation').innerHTML=recHTML(rec)}
function card(o){if(!o||typeof o!=='object')return esc(val(o));return '<dl>'+Object.entries(o).map(([k,v])=>`<dt><b>${esc(k)}</b></dt><dd>${typeof v==='object'?card(v):esc(val(v))}</dd>`).join('')+'</dl>'}
function provHTML(p){return `<p><b>${esc(p.kind)}</b></p>${card(p.summary||{})}<h4>source bindings</h4><ul>${(p.bindings||[]).map(b=>`<li>${esc(b.source_schema)}.${esc(b.source_table)} · ${esc(b.trust_status)}</li>`).join('')||'<li>血缘链接尚不完整</li>'}</ul><p>${esc((p.gaps||[]).join('；'))}</p>`}
function paperConclusion(r){const pr=r.paper_retrieval||{},direct=Number(pr.direct_count||0),nearby=Number(pr.nearby_count||0),claims=Number(pr.eligible_claim_count||0);if(pr.status==='DIRECT_MATCH_AVAILABLE')return`存在 ${direct} 篇直接匹配论文，当前展示 ${claims} 条合格 Claim。`;if(pr.status==='NO_DIRECT_MATCH_NEARBY_AVAILABLE'||pr.status==='NO_DIRECT_PAPER_MATCH_WITH_NEARBY_EVIDENCE')return`没有直接匹配论文，但存在 ${nearby} 篇邻近论文和 ${claims} 条合格 Claim。当前推荐由项目规则和邻近论文证据共同支持，并非“没有相关论文”。`;if(pr.status==='PAPER_INDEX_NOT_READY')return'论文索引尚未就绪，暂时不能判断是否存在相关论文。';if(pr.status==='NO_ELIGIBLE_PAPER_AFTER_COMPLETED_SEARCH')return'论文检索已完成，但没有合格直接或邻近论文；当前仅由项目规则支持。';return'论文检索失败或状态未知，不能作出“没有论文”的结论。'}
function modelCard(m,label){return `<div class="rec-card"><h4>${esc(label)}：${esc(modelName(m))}</h4><p><b>当前状态：</b>${esc(val(m?.current_executable))}</p><p><b>原因：</b>${esc(m?.reason||m?.capability||'未提供')}</p><p><b>最低条件：</b>${esc(val(m?.minimum_data,'minimum'))}</p><p><b>当前差距：</b>${esc(val(m?.current_gap,'gap'))}</p></div>`}
function recHTML(r){const cur=r.current_readiness||{},plan=r.conditional_full_data_plan||{},pref=plan.preferred_model||{},alts=plan.conditional_alternatives||[],notp=plan.not_preferred_models||[],pr=r.paper_retrieval||{},papers=r.paper_matches||[],claims=r.claim_matches||[];return `<section class="rec-readable"><h3>当前数据准备度</h3><p>${pill(cur.status)} 目前可执行：${esc((cur.eligible_actions||[]).map(human).join('、')||'展示预览')}</p><h3>模型推荐</h3>${(r.current_baselines||[]).map((m,i)=>modelCard(m,`当前 baseline #${i+1}`)).join('')}${modelCard(pref,'数据补齐后的首选模型')}${alts.map((m,i)=>modelCard(m,`条件候选 #${i+1}`)).join('')}${notp.map((m,i)=>modelCard(m,`当前不推荐 #${i+1}`)).join('')}<h3>论文检索结论</h3><p>${pill(pr.status)} ${esc(paperConclusion(r))}</p>${table(['计数项','数量'],[['direct_eligible_paper_count',pr.direct_count],['nearby_eligible_paper_count',pr.nearby_count],['eligible_claim_count',pr.eligible_claim_count],['excluded_paper_or_claim_count',pr.excluded_paper_or_claim_count],['paper1 participation',0]].map(([k,v])=>[esc(k),esc(v??0)]))}<h3>推荐依据</h3><ol>${(r.audit_rationale||[]).map(a=>`<li>${esc(a.fact)}；来源：${esc(a.source)}；规则：${esc(a.rule_id)}；限制：${esc(a.limitation)}</li>`).join('')}</ol><h3>直接或邻近论文</h3>${papers.length?papers.map(p=>`<div class="rec-card"><b>${esc(p.paper_title||p.paper_id)}</b><p>Claim：${esc(p.claim_text_or_paraphrase)}</p><p>定位：物理页 ${esc(p.locator?.physical_page||'未提供')} / 印刷页 ${esc(p.locator?.printed_page||'未提供')} / ${esc(p.locator?.section||'未提供')}</p><p>迁移提醒：${esc(p.transfer_warning||'邻近证据需谨慎迁移')}</p></div>`).join(''):'<p>当前没有可展示论文；见上方状态。</p>'}<h3>Claim 和证据定位</h3><ul>${claims.map(c=>`<li>${esc(c.claim_text_or_paraphrase||c.claim_id)}；定位：${esc(c.locator?.section||'未提供')}</li>`).join('')||'<li>没有可展示的合格 Claim。</li>'}</ul><h3>数据缺口和验证计划</h3><ul>${((r.data_gap_plan||{}).items||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></section>`}
function draw(g){let svg=$('graph');svg.innerHTML='';let nodes=g.nodes||[],edges=g.edges||[];let pos={};nodes.forEach((n,i)=>{let angle=2*Math.PI*i/Math.max(1,nodes.length);let r=i?145:0;pos[n.node_id]={x:460+r*Math.cos(angle),y:190+r*Math.sin(angle)}});edges.forEach(e=>{let a=pos[e.subject_id],b=pos[e.object_id];if(!a||!b)return;svg.insertAdjacentHTML('beforeend',`<line class="edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"></line><text x="${(a.x+b.x)/2}" y="${(a.y+b.y)/2}" font-size="11">${esc(e.predicate)}</text>`)});nodes.forEach(n=>{let p=pos[n.node_id];svg.insertAdjacentHTML('beforeend',`<g class="node" data-id="${n.node_id}"><circle cx="${p.x}" cy="${p.y}" r="24" fill="${n.node_type==='DATA_SERIES'?'#1c5aa6':'#6b8e23'}"></circle><text x="${p.x}" y="${p.y+42}" text-anchor="middle" font-size="11">${esc(n.display_name)}</text></g>`)});const rel={ABOUT_GEOGRAPHY:'关于国家/地区',ABOUT_DISEASE:'关于病种',PREDICTS_TARGET:'预测目标',USES_SOURCE:'使用数据源'};$('edgeList').innerHTML=table(['起点','关系中文/内部类型','终点'],edges.map(e=>[esc(e.subject_name),`${esc(rel[e.predicate]||e.predicate)}<br><small>${esc(e.predicate)}</small>`,esc(e.object_name)]));$('graphMeta').textContent=`节点 ${g.node_count}，边 ${g.edge_count}，深度 ${g.depth}，上限 ${g.limit}。`}
async function entitySearch(){let q=encodeURIComponent($('entityQ').value),t=encodeURIComponent($('entityType').value),inc=$('includeQ').checked;let d=await j(`/api/v1/kg/entities?q=${q}&node_type=${t}&include_quarantined=${inc}`);$('entityResults').innerHTML=table(['类型','名称','状态','隔离'],d.items.map(x=>[esc(x.node_type),esc(x.display_name),esc(x.review_status),x.quarantine?'是':'否']))}
$('runQuery').onclick=query;$('entityBtn').onclick=entitySearch;loadOptions().then(query).catch(e=>$('summary').textContent='初始化错误：'+e.message);
"""

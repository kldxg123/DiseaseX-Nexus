
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine, text

from .benchmark import load_sgp_dengue_paper_benchmark
from .benchmark_rsos import load_rsos_mpox_paper_benchmark
from .loader import load_graph
from .r4_shadow import ForecastOrchestrator, ModelRegistry
from .service import (
    disease_graph_response,
    full_graph_response,
    guidance_response,
    metadata_response,
)


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


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


KG_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>预测模型知识图谱</title>
  <link rel="stylesheet" href="/static/forecast-model-kg.css">
</head>
<body class="kg-page kg-preexecution-page">
  <header>
    <div>
       <h1>预测模型知识图谱</h1>
       <p>根据当前数据特点、疾病机制和论文证据，解释“为什么推荐这个模型”。</p>
    </div>
      <nav><a href="/">数据仪表板</a><a href="/kg/explorer">知识图谱浏览</a><a href="/predictions">模型预测</a><a href="/fasttrack-forecast">快速预测</a></nav>
      <form method="post" action="/logout"><button type="submit">退出登录</button></form>
  </header>
  <main>
    <section class="kg-warning" id="preexecutionDisclaimer">
        <strong>使用说明：</strong>
        本页展示的是“模型运行前的证据推荐”，不是已经产生的预测结果，也不代表预测精度。所有候选目前均为“尚未执行模型”；旧版数据只保留作审计，不再驱动本页。
    </section>
    <section class="kg-controls" aria-label="preexecution selector">
      <label>国家 <select id="kgCountry"></select></label>
      <label>病种 <select id="kgDisease"></select></label>
      <label>指标 <select id="kgMetric"></select></label>
      <label>频率 <select id="kgCadence"></select></label>
      <button id="kgRun" type="button">运行评估</button>
    </section>
     <section id="kgStatus" class="kg-status" aria-live="polite">正在读取模型证据评分…</section>
     <section class="kg-grid">
       <article class="kg-wide"><h2>模型候选</h2><p class="kg-section-help">先选择一个候选查看明细；推荐理由、评分依据和论文证据会在下方同步切换。</p><div id="kgModels"></div></article>
       <article class="kg-wide kg-direct-answer"><h2>为什么推荐这个模型？</h2><div id="kgDirectAnswer"></div></article>
       <article class="kg-wide"><h2>六个维度的通俗评分说明</h2><p class="kg-section-help">A–F 分别回答数据、疾病、国家场景、论文证据、参数和执行条件六类问题。分数越高表示当前证据越充分，不等同于预测准确率。</p><div id="kgWaterfall"></div></article>
       <article><h2>当前数据画像</h2><div id="kgMatrix"></div></article>
       <article><h2>模型参数与来源</h2><div id="kgParams"></div></article>
       <article class="kg-wide"><h2>论文证据与原文定位</h2><p class="kg-section-help">默认展示精简摘要；展开后可查看原文、页码、文本偏移和哈希。证据用于支持模型或参数适用性，未必都来自同一病种。</p><div id="kgEvidence"></div></article>
       <article class="kg-wide"><h2>推荐关系图</h2><p class="kg-section-help">图中只展示关系类型，长编号和哈希收纳在其他审计区块，避免文字溢出。</p><svg id="kgGraph" role="img" aria-label="模型推荐关系图"></svg><div id="kgTableFallback"></div></article>
       <article><h2>未通过项与基线说明</h2><div id="kgExcluded"></div></article>
       <article><h2>执行状态与安全边界</h2><div id="kgDeferred"></div><button disabled>当前仅推荐，不运行模型</button></article>
     </section>
     <section id="kg-r3c">
       <h2>知识图谱数据维护状态</h2>
       <p>本节面向审计人员，展示资料导入、图版本完整性和人工复核队列。当前没有调用模型训练或预测。</p>
      <div id="kgR3cStatus" class="kg-status" aria-live="polite">正在加载 R3C 数据…</div>
      <div class="kg-grid">
        <article><h2>导入核算</h2><div id="kgR3cImports"></div></article>
         <article><h2>知识图谱版本与完整性</h2><div id="kgR3cGraph"></div></article>
        <article><h2>复核队列</h2><div id="kgR3cReview"></div></article>
      </div>
    </section>
    <section id="kg-dual-loop">
      <h2>知识图谱预测闭环（Shadow）</h2>
      <p>本节展示 KG 推荐模型在隔离 Shadow 环境中的真实执行结果：滚动回测、与基线模型对比、客观反馈与受控学习闭环。所有结果均为 Shadow，未发布、未晋级、不影响任何正式预测。</p>
      <div class="kg-controls" aria-label="dual loop selector">
        <label>数据集
          <select id="kgDlDataset">
            <option value="AUT">奥地利 / 军团菌病 / 月度（历史拟真）</option>
            <option value="PER">秘鲁 / 流感 / 周度（Demo Shadow）</option>
          </select>
        </label>
        <button id="kgDlRun" type="button">执行 / 刷新 Shadow 闭环</button>
      </div>
      <div id="kgDlStatus" class="kg-status" aria-live="polite">选择数据集后点击按钮加载闭环结果…</div>
      <div class="kg-grid">
        <article><h2>数据画像</h2><div id="kgDlProfile"></div></article>
        <article><h2>KG 推荐</h2><div id="kgDlRec"></div></article>
        <article><h2>模型执行</h2><div id="kgDlRunInfo"></div></article>
        <article class="kg-wide"><h2>回测：推荐模型 vs 基线</h2><div id="kgDlBacktest"></div></article>
        <article class="kg-wide"><h2>预测结果（历史真实值 + H1–H4）</h2><svg id="kgDlChart" role="img" aria-label="预测对比图"></svg><div id="kgDlForecast"></div></article>
        <article class="kg-wide"><h2>反馈与学习闭环</h2><div id="kgDlFeedback"></div>
          <form id="kgDlFeedbackForm" class="kg-feedback-form">
            <label>评估人 <input name="actor" required maxlength="64" placeholder="姓名或工号"></label>
            <label>结论
              <select name="decision_code">
                <option value="USEFUL">有用（USEFUL）</option>
                <option value="NOT_USEFUL">无用（NOT_USEFUL）</option>
                <option value="EXPLANATION_CLEAR">解释清楚（EXPLANATION_CLEAR）</option>
                <option value="EXPLANATION_UNCLEAR">解释不清（EXPLANATION_UNCLEAR）</option>
                <option value="DATA_QUALITY_CONCERN">数据质量疑虑（DATA_QUALITY_CONCERN）</option>
                <option value="NEEDS_MORE_EVIDENCE">需要更多证据（NEEDS_MORE_EVIDENCE）</option>
              </select>
            </label>
            <label>评分 <input name="rating" type="number" min="1" max="5" step="1"></label>
            <label>备注 <input name="comment" maxlength="500" placeholder="可选"></label>
            <button type="submit">提交人工反馈</button>
          </form>
          <div id="kgDlFeedbackMsg" class="kg-status" aria-live="polite"></div>
        </article>
        <article class="kg-wide"><h2>限制说明</h2><div id="kgDlLimits"></div></article>
      </div>
    </section>
    <section id="kg-param-loop">
      <h2>参数优化与学习闭环（Parameter Tuning &amp; Learning Loop）</h2>
      <section class="kg-warning" id="kgPtDisclaimer">
        <strong>安全声明：</strong>本节全部结果处于 <b>Shadow</b> 状态（未发布、未晋级、不影响任何正式预测）；
        数据为<b>历史回放</b>（DEMO_HISTORICAL_REPLAY），不代表实时疫情；所有内容<b>不构成医疗或公共卫生结论</b>。
      </section>
      <div class="kg-controls" aria-label="pt demo controls">
        <button id="kgPtRun" type="button">运行 / 刷新 参数调优闭环</button>
      </div>
      <div id="kgPtRunInfo" class="kg-status" aria-live="polite">正在加载参数优化闭环…</div>
      <div class="kg-grid">
        <article><h2>当前数据集</h2><div id="kgPtContext"></div></article>
        <article><h2>KG 推荐与理由</h2><div id="kgPtRec"></div></article>
        <article class="kg-wide"><h2>数据时间轴（训练 / 调参验证 / 独立测试）</h2><svg id="kgPtTimeline" role="img" aria-label="数据切分时间轴"></svg><div id="kgPtTimelineLegend"></div></article>
        <article class="kg-wide"><h2>参数空间与最优参数</h2><div id="kgPtParams"></div></article>
        <article class="kg-wide"><h2>调参过程（trial 序号 vs 共同样本 WAPE）</h2><svg id="kgPtTuning" role="img" aria-label="调参过程图"></svg><div id="kgPtTuningNote"></div></article>
        <article class="kg-wide"><h2>调参前后验证指标与基线对比</h2><div id="kgPtMetrics"></div></article>
        <article class="kg-wide"><h2>独立测试评估</h2><div id="kgPtTest"></div></article>
        <article class="kg-wide"><h2>H1–H4 实际值 vs 预测（含区间带）</h2><svg id="kgPtForecast" role="img" aria-label="预测对比图"></svg><div id="kgPtForecastNote"></div></article>
        <article><h2>当前 Champion 与胜负原因</h2><div id="kgPtChampion"></div></article>
        <article><h2>反馈成熟进度</h2><div id="kgPtFeedback"></div></article>
        <article class="kg-wide"><h2>学习闭环时间线</h2><div id="kgPtCycles"></div></article>
        <article class="kg-wide"><h2>限制说明</h2><div id="kgPtLimits"></div></article>
      </div>
    </section>
    <section id="kg-qwen-loop">
      <h2>Qwen 自动调优与学习闭环（Qwen Autotuning Loop）</h2>
      <section class="kg-warning" id="kgQDisclaimer">
        <strong>安全声明：</strong>本节全部结果处于 <b>Shadow</b> 状态（未发布、未晋级、不影响任何正式预测）；
        演示数据为<b>历史回放</b>（非实时疫情数据）；所有内容<b>不构成医疗或公共卫生结论</b>；
        <b>预测吻合度为 WAPE 换算指标，不是分类准确率，也不保证未来准确</b>。
      </section>
      <div class="kg-controls" aria-label="qwen autotuning controls">
        <button id="kgQTrackA" type="button" aria-pressed="true">轨道 A：AUT / 军团菌病 / 月（历史回放演示）</button>
        <button id="kgQTrackB" type="button" aria-pressed="false">轨道 B：FIN / 汉坦病毒 / 周（历史重复评估，非盲测）</button>
        <button id="kgQTrackC" type="button" aria-pressed="false">轨道 C：CHL / 流感 / 周（新鲜盲测）</button>
        <button id="kgQTrackD" type="button" aria-pressed="false">轨道 D：MYS / 流感 / 周（第三条候选链）</button>
        <button id="kgQRun" type="button">运行 / 刷新 当前轨道的 Qwen 自动调优</button>
      </div>
      <div id="kgQTrackNote" class="kg-status"></div>
      <div id="kgQRunInfo" class="kg-status" aria-live="polite">正在加载 Qwen 自动调优面板…</div>
      <div class="kg-grid">
        <article><h2>当前轨道（国家 / 病种 / 指标 / 频率）</h2><div id="kgQContext"></div></article>
        <article><h2>数据画像（仅调优窗统计）</h2><div id="kgQProfile"></div></article>
        <article class="kg-wide"><h2>KG 初始推荐与理由</h2><div id="kgQRec"></div></article>
        <article class="kg-wide"><h2>Qwen 每轮调优方案</h2><div id="kgQPlans"></div></article>
        <article class="kg-wide"><h2>参数范围变化（合同范围 vs 本轮范围）</h2><div id="kgQParamRanges"></div></article>
        <article class="kg-wide"><h2>每轮 trial 数与验证指标</h2><div id="kgQRounds"></div></article>
        <article class="kg-wide"><h2>调参过程（trial 序号 vs 共同样本 WAPE）</h2><svg id="kgQTuning" role="img" aria-label="Qwen 调参过程图"></svg><div id="kgQTuningNote"></div></article>
        <article class="kg-wide"><h2>Held-out 独立测试结果与治理状态</h2><div id="kgQEval"></div></article>
        <article class="kg-wide"><h2>同样本基线对比（paired）</h2><div id="kgQPaired"></div></article>
        <article><h2>当前 Champion 与胜负原因</h2><div id="kgQChampion"></div></article>
        <article><h2>反馈成熟进度</h2><div id="kgQFeedback"></div></article>
        <article class="kg-wide"><h2>学习闭环时间线</h2><div id="kgQCycles"></div></article>
        <article class="kg-wide"><h2>运行元信息（可复现性审计）</h2><div id="kgQMeta"></div></article>
        <article class="kg-wide"><h2>限制说明</h2><div id="kgQLimits"></div></article>
      </div>
    </section>
    <section id="kg-paper-benchmark-rsos">
      <h2>主要正式论文对比实验：Bleichrodt et al. 2024（R. Soc. Open Sci.，JCR Q1/Q2）</h2>
      <section class="kg-warning">
        <strong>审计身份：</strong>正式同行评审论文方法的 METHOD_TRANSFER_REPLICATION（方法迁移复现，非论文原数据逐字节复现，非 Fresh 盲测）。同数据、同切分、同 horizon、同样本配对；不发布、不晋级、不修改 Active pointer。
      </section>
      <div id="kgBenchmarkRsosStatus" class="kg-status" aria-live="polite">正在读取正式论文对比链路…</div>
      <div class="kg-grid">
        <article><h2>论文身份与等级</h2><div id="kgBenchmarkRsosPaper"></div></article>
        <article><h2>数据与切分</h2><div id="kgBenchmarkRsosData"></div></article>
        <article class="kg-wide"><h2>同协议方法对照（测试集，paired）</h2><div id="kgBenchmarkRsosComparators"></div></article>
        <article class="kg-wide"><h2>项目方法与 Qwen 受控调优</h2><div id="kgBenchmarkRsosProject"></div></article>
        <article class="kg-wide"><h2>区间与分 horizon 指标</h2><div id="kgBenchmarkRsosHorizon"></div></article>
        <article class="kg-wide"><h2>证据定位与可复现审计</h2><div id="kgBenchmarkRsosAudit"></div></article>
      </div>
    </section>
    <section id="kg-paper-benchmark-e">
      <h2>历史论文对齐回放：新加坡登革热（LEGACY_PREPRINT_NOT_PRIMARY_BENCHMARK）</h2>
      <section class="kg-warning">
        <strong>审计身份：</strong>论文对齐历史回放（非 Fresh 盲测）。对应论文（arXiv:2407.00332）经核验仅有 arXiv 预印本，无正式同行评审发表版本，不再作为正式高水平论文对比实验；历史证据保留，不删除不覆盖。不发布、不晋级、不修改 Active pointer。
      </section>
      <div id="kgBenchmarkEStatus" class="kg-status" aria-live="polite">正在读取论文对齐链路…</div>
      <div class="kg-grid">
        <article><h2>数据与切分</h2><div id="kgBenchmarkEData"></div></article>
        <article><h2>知识图谱推荐方案</h2><div id="kgBenchmarkERecommendation"></div></article>
        <article class="kg-wide"><h2>测试集结果与阈值</h2><div id="kgBenchmarkEMetrics"></div></article>
        <article class="kg-wide"><h2>论文方法与项目方法同协议对照</h2><div id="kgBenchmarkEComparators"></div></article>
        <article class="kg-wide"><h2>可复现与治理说明</h2><div id="kgBenchmarkEAudit"></div></article>
      </div>
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

    @app.get("/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-240248-method-transfer-sgp-dengue")
    def rsos_240248_method_transfer_benchmark_api(request: Request) -> Response:
        """Canonical read-only route for the RSOS 240248 method-transfer benchmark.

        The paper object is mpox (Bleichrodt et al. 2024); the evaluation object
        is the Singapore dengue weekly series. The legacy alias
        ``rsos-mpox-2024`` below is retained for link compatibility.
        """
        if not authorized(request):
            return unauthorized()
        try:
            return json_response(load_rsos_mpox_paper_benchmark(), 200)
        except (OSError, ValueError, json.JSONDecodeError):
            return json_response({"detail": "RSOS_240248_BENCHMARK_LOAD_FAILED"}, 503)

    @app.get("/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-mpox-2024")
    def rsos_mpox_paper_benchmark_api(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        try:
            return json_response(load_rsos_mpox_paper_benchmark(), 200)
        except (OSError, ValueError, json.JSONDecodeError):
            return json_response({"detail": "RSOS_MPOX_BENCHMARK_LOAD_FAILED"}, 503)


    @app.get("/api/v1/forecast-model-knowledge-graph/benchmarks/sgp-dengue")
    def sgp_dengue_paper_benchmark_api(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        try:
            return json_response(load_sgp_dengue_paper_benchmark(), 200)
        except (OSError, ValueError, json.JSONDecodeError):
            return json_response({"detail": "SGP_DENGUE_BENCHMARK_LOAD_FAILED"}, 503)

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



    @app.get("/api/v1/forecast-model-kg/r3a/preexecution/catalog")
    def forecast_model_kg_r3a_catalog(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        with engine.connect() as connection:
            rows = connection.execute(text("""
                select distinct p.country_alpha3, p.disease_slug, p.metric_code, p.cadence
                from epidemic_kg.kg_preexecution_model_candidates c
                join epidemic_kg.kg_preexecution_data_profiles p on p.profile_id=c.profile_id
                where c.preexecution_evidence_score > 70
                  and c.parameter_confidence_score > 70
                  and c.actually_executed=false and c.forecast_run_id is null
                order by p.country_alpha3, p.disease_slug, p.metric_code, p.cadence
            """)).mappings().all()
        return json_response({"score_type":"PRE_EXECUTION_EVIDENCE_SCORE","items":[dict(r) for r in rows]}, 200)

    @app.get("/api/v1/forecast-model-kg/r3a/preexecution/candidates")
    def forecast_model_kg_r3a_candidates(
        request: Request,
        country: str | None = None,
        disease: str | None = None,
        metric: str | None = None,
        cadence: str | None = None,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        params={"country":country,"disease":disease,"metric":metric,"cadence":cadence}
        with engine.connect() as connection:
            rows = connection.execute(text("""
                select c.candidate_id::text, c.run_id::text, p.profile_id::text,
                       p.country_alpha3, p.disease_slug, p.metric_code, p.cadence,
                       p.period_start, p.period_end, p.observed_count, p.nonzero_count, p.source_count,
                       b.blueprint_id, b.display_name, b.model_family,
                       c.preexecution_evidence_score, c.parameter_confidence_score, c.evidence_coverage,
                       c.hard_gates, c.rank, c.explanation_zh,
                       c.actually_executed, c.fit_called, c.predict_called, c.local_backtest_status,
                       c.runtime_validation_status, c.forecast_run_id, c.forecast_value_count, c.local_metric_count,
                       c.formal_recommendation_eligible, c.promotion_eligible, c.active_pointer_eligible, c.accuracy_claim_allowed
                from epidemic_kg.kg_preexecution_model_candidates c
                join epidemic_kg.kg_preexecution_data_profiles p on p.profile_id=c.profile_id
                join epidemic_kg.kg_preexecution_model_blueprints b on b.blueprint_id=c.blueprint_id
                where (cast(:country as text) is null or p.country_alpha3=cast(:country as text))
                  and (cast(:disease as text) is null or p.disease_slug=cast(:disease as text))
                  and (cast(:metric as text) is null or p.metric_code=cast(:metric as text))
                  and (cast(:cadence as text) is null or p.cadence=cast(:cadence as text))
                  and b.is_baseline=false
                  and c.preexecution_evidence_score > 70 and c.parameter_confidence_score > 70
                order by c.rank, c.preexecution_evidence_score desc, c.parameter_confidence_score desc
                limit 50
            """), params).mappings().all()
        return json_response(_jsonable({
          "score_type":"PRE_EXECUTION_EVIDENCE_SCORE",
          "disclaimer":"本分数为执行前证据评分，非预测精度或本地回测性能承诺。",
          "items":[dict(r) for r in rows],
          "baseline_in_preexecution_recommendation_count":0,
        }), 200)

    @app.get("/api/v1/forecast-model-kg/r3a/preexecution/candidates/{candidate_id}")
    def forecast_model_kg_r3a_candidate_detail(request: Request, candidate_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        with engine.connect() as connection:
            cand = connection.execute(text("""
                select c.*, p.*, b.display_name, b.model_family, b.blueprint, s.spec, s.parameter_confidence_score as bundle_parameter_confidence_score
                from epidemic_kg.kg_preexecution_model_candidates c
                join epidemic_kg.kg_preexecution_data_profiles p on p.profile_id=c.profile_id
                join epidemic_kg.kg_preexecution_model_blueprints b on b.blueprint_id=c.blueprint_id
                join epidemic_kg.kg_preexecution_parameter_bundle_specs s on s.bundle_id=c.bundle_id
                where c.candidate_id=cast(:candidate_id as uuid)
            """), {"candidate_id": candidate_id}).mappings().first()
            if not cand:
                return json_response({"detail":"CANDIDATE_NOT_FOUND"}, 404)
            comps = connection.execute(text("""
                select component_id::text, component_group, rule_id, score, max_score, contribution, evidence_point_ids
                from epidemic_kg.kg_preexecution_score_components where candidate_id=cast(:candidate_id as uuid)
                order by component_group, rule_id
            """), {"candidate_id": candidate_id}).mappings().all()
            evidence = connection.execute(text("""
                select point_id, model_key, disease_slug, point_type, polarity, short_source_excerpt, locator, score_contribution
                from epidemic_kg.kg_preexecution_evidence_points
                where recommendation_eligible=true and (model_key=:model_key or disease_slug=:disease_slug)
                order by point_id limit 20
            """), {"model_key": cand["blueprint_id"], "disease_slug": cand["disease_slug"]}).mappings().all()
        return json_response(_jsonable({
          "score_type":"PRE_EXECUTION_EVIDENCE_SCORE",
          "candidate":dict(cand),
          "score_components":[dict(r) for r in comps],
          "evidence_points":[dict(r) for r in evidence],
          "runtime_deferred":{"fit":False,"predict":False,"forecast":False,"backtest":"DEFERRED_TO_R3B"},
          "audit_statement":"No local model execution, no forecast run, no forecast value, no backtest metric."
        }), 200)

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

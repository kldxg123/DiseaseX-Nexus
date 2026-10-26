# ruff: noqa: E501
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import urllib.parse
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import Engine, create_engine, text

from epidemic_core.ebola.dashboard_app import install_routes as install_ebola_routes
from epidemic_core.forecast_model_kg.dashboard_app import install_routes as install_forecast_model_kg_routes
from epidemic_core.kg_p1.dashboard_app import install_routes as install_kg_p1_routes
from epidemic_core.kg_r3c.dashboard_app import install_routes as install_kg_r3c_routes
from epidemic_core.kg_pt.api import install_pt_routes
from epidemic_core.kg_r3c.dual_loop_api import install_dual_loop_routes
from epidemic_core.p1_10.dashboard_app import install_routes as install_preview_routes
from epidemic_core.p1_10.diagnostics import readiness, request_id, unexpected_error
from epidemic_core.p1_12.dashboard_app import install_routes as install_p1_12_routes

SESSION_SECONDS = 8 * 60 * 60
MAX_POINTS = 5000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
COOKIE_NAME = "epidemic_dashboard_session"
STATIC_VERSION = "p1-7ar-v1"


class AuthStore:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.username = str(raw.get("username", "analyst"))
        self.salt = bytes.fromhex(str(raw["salt_hex"]))
        self.expected = bytes.fromhex(str(raw["hash_hex"]))
        self.n, self.r, self.p = int(raw.get("n", 16384)), int(raw.get("r", 8)), int(raw.get("p", 1))

    def verify(self, username: str, password: str) -> bool:
        candidate = hashlib.scrypt(password.encode(), salt=self.salt, n=self.n, r=self.r, p=self.p, dklen=32)
        return hmac.compare_digest(username.encode(), self.username.encode()) & hmac.compare_digest(
            candidate, self.expected
        )


class SessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, float] = {}
        self.failures: defaultdict[str, deque[float]] = defaultdict(deque)
        self.locked_until: dict[str, float] = {}

    def authenticated(self, token: str | None) -> bool:
        if not token or self.sessions.get(token, 0) <= time.time():
            self.sessions.pop(token or "", None)
            return False
        return True

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.time() + SESSION_SECONDS
        return token

    def failed(self, key: str) -> None:
        now = time.time()
        events = self.failures[key]
        while events and events[0] < now - 600:
            events.popleft()
        events.append(now)
        if len(events) >= 5:
            self.locked_until[key] = now + 300

    def allowed(self, key: str) -> bool:
        return self.locked_until.get(key, 0) <= time.time()

    def succeeded(self, key: str) -> None:
        self.failures.pop(key, None)
        self.locked_until.pop(key, None)


def _json_value(value: Any) -> Any:
    return str(value) if isinstance(value, (dt.date, dt.datetime, dt.time, uuid.UUID, Decimal)) else value


def _row(row: Any) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row.items()}


def _json_response(value: Any, status_code: int = 200) -> JSONResponse:
    if len(json.dumps(value, ensure_ascii=False, default=str).encode()) > MAX_RESPONSE_BYTES:
        return JSONResponse({"detail": "RESPONSE_TOO_LARGE"}, status_code=413)
    return JSONResponse(value, status_code=status_code)


def _strict_date(value: str | None) -> dt.date | None:
    if value is None:
        return None
    if len(value) != 10:
        raise ValueError("INVALID_DATE")
    parsed = dt.date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("INVALID_DATE")
    return parsed


def _query(engine: Engine, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(text(sql), dict(params or {})).mappings().all()
    return [_row(row) for row in rows]


def _authorized(request: Request) -> bool:
    return bool(request.app.state.sessions.authenticated(request.cookies.get(COOKIE_NAME)))


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "AUTHENTICATION_REQUIRED"}, status_code=401)


LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>疫情仪表板登录</title><link rel="stylesheet" href="/static/dashboard.p1-7ar-v1.css"></head><body class="login-page"><main class="login-card"><h1>疫情数据仪表板</h1><p>请输入本地保存的访问凭据。</p>{error}<form method="post" action="/login" autocomplete="off"><label for="username">用户名</label><input id="username" name="username" required maxlength="64"><label for="password">密码</label><input id="password" name="password" type="password" required maxlength="256"><button type="submit">登录</button></form></main><script src="/static/dashboard.prediction-demo.js" defer></script></body></html>"""

DASHBOARD_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>只读疫情数据三联图</title><link rel="stylesheet" href="/static/dashboard.p1-7ar-v1.css"></head><body><header><div><h1>只读疫情数据三联图</h1><p>正式数据库中的报告期数据，不执行插值、平滑或预测。</p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><main><section class="status-panel" aria-live="polite"><strong id="state-label">INITIALIZING</strong><span id="state-message">正在初始化…</span><button id="retry" type="button" hidden>重试</button></section><section class="filters" aria-label="数据筛选"><label for="country-input">国家（中文、英文、ISO2/ISO3）</label><input id="country-input" role="combobox" aria-autocomplete="list" aria-controls="country-list" list="country-list" autocomplete="off"><datalist id="country-list"></datalist><label for="disease-input">病种（中文、英文、canonical code）</label><input id="disease-input" role="combobox" aria-autocomplete="list" aria-controls="disease-list" list="disease-list" autocomplete="off"><datalist id="disease-list"></datalist><label for="source-select">数据源</label><select id="source-select"><option value="all">全部来源</option></select><label for="view-select">数据视图</label><select id="view-select"><option value="all">全部已保存数据</option><option value="latest">最新 ready snapshot</option><option value="snapshot">指定不可变 snapshot</option></select><label for="snapshot-select">不可变 snapshot</label><select id="snapshot-select" disabled></select><label for="start-date">开始日期（可空）</label><input id="start-date" type="date"><label for="end-date">结束日期（可空）</label><input id="end-date" type="date"><button id="clear-dates" type="button">清除时间范围</button><button id="load-data" type="button">加载数据</button></section><section id="empty" class="notice" hidden></section><section id="summary" class="summary" aria-label="当前数据摘要"></section><section class="charts" aria-label="疫情三联图"><article><h2>图1：新增报告病例</h2><p class="chart-note">报告期数据，不代表实际感染发生日期。</p><div id="cases-legend" class="legend"></div><svg id="cases-chart" role="img" aria-label="新增报告病例图" tabindex="0"></svg><p id="cases-count"></p><div id="cases-table"></div></article><article><h2>图2：新增报告死亡</h2><p class="chart-note">报告期数据，不代表实际死亡发生日期。</p><p id="death-empty" class="notice" hidden>该来源没有提供这一时期的新增死亡数据，不等于死亡数为0。</p><div id="deaths-legend" class="legend"></div><svg id="deaths-chart" role="img" aria-label="新增报告死亡图" tabindex="0"></svg><p id="deaths-count"></p><div id="deaths-table"></div></article><article><h2>图3：质量、缺失、新鲜度与训练资格</h2><p class="chart-note">质量标记保留来源证据；负修订原样显示，不会被改写。</p><div id="quality-legend" class="legend"></div><svg id="quality-chart" role="img" aria-label="数据质量图" tabindex="0"></svg><p id="quality-count"></p><div id="quality-table"></div></article></section><section id="prediction-demo-panel" class="prediction-demo" aria-labelledby="prediction-demo-title">
<h2 id="prediction-demo-title">Outbreak Onset Risk Prediction（演示）</h2>
<p class="preview-warning">DEMO ONLY / NOT FOR PUBLIC-HEALTH DECISIONS。真实 Trusted 数据当前不足以训练经验证的 onset 模型；此区块只展示 SAMPLE_DATA / PIPELINE_SMOKE_TEST 工程链路。</p>
<p class="preview-warning">REAL OFFICIAL DATA / WHO FluNet / RETROSPECTIVE ENGINEERING DEMO / NOT A VALIDATED PUBLIC-HEALTH WARNING SYSTEM / DOES NOT VALIDATE EBOLA PREDICTION.</p>
<div id="prediction-demo-status" class="notice">正在读取预测演示产物…</div>
<div id="prediction-demo-summary" class="summary-grid"></div>
<div id="prediction-demo-table" class="table-box"></div>
</section></main><script src="/static/dashboard.p1-7ar-v1.js" defer></script></body></html>"""



TRIPTYCH_NO_PREDICTION_SCOPE_LOCK = True
DASHBOARD_HTML = re.sub(r'<section id="prediction-demo-panel".*?</section>', "", DASHBOARD_HTML, flags=re.S)

DASHBOARD_HTML = DASHBOARD_HTML.replace(
    "<main>",
    """<main><section class="daily-update" aria-labelledby="daily-update-title">
    <div class="daily-update-heading"><div><h2 id="daily-update-title">每日自动更新状态</h2>
    <p>每天自动检查已批准来源；来源无变化时不会重复生成数据或快照。</p></div>
    <strong id="daily-overall-state">正在读取…</strong></div>
    <div id="daily-update-grid" class="daily-update-grid" aria-live="polite"></div>
    <p class="daily-update-note">计划时间按 Asia/Shanghai 显示，同时显示浏览器本地时间。服务器关机期间无法抓取，服务再次启动时会合并补跑一次。</p>
    </section>""",
    1,
)
DASHBOARD_HTML = DASHBOARD_HTML.replace(
    "</header>",
    "<p><a href='/p1-12/trusted'>查看 P1-12 可信 Ebola / Influenza 正式数据</a> · <a href='/ebola-preview'>查看历史埃博拉候选 Preview</a> · <a href='/legionellosis-preview'>美国军团菌病 Preview</a></p></header>",
    1,
)
DASHBOARD_HTML = DASHBOARD_HTML.replace(
    "<main>",
    """<main><section class="preview-tier-switch"><label for="p1-10-data-tier">数据层级</label>
    <select id="p1-10-data-tier"><option value="TRUSTED">可信正式数据（默认）</option>
    <option value="REVIEW_PREVIEW">候选演示数据</option></select></section>
    <section id="p1-10-preview-panel" hidden><p class="preview-warning">候选演示数据：未经正式批准，不代表完整或全国疫情数据，不用于训练、预测、每日更新或公共结论。</p>
    <section class="filters" aria-label="候选 Preview 筛选"><label>国家<select id="p1-10-preview-country" disabled></select></label><label>病种<select id="p1-10-preview-disease" disabled></select></label>
    <label>数据来源<select id="p1-10-preview-source"></select></label><label>指标<select id="p1-10-preview-metric" disabled></select></label>
    <label>开始日期<input id="p1-10-preview-start" type="date"></label><label>结束日期<input id="p1-10-preview-end" type="date"></label>
    <label>州代码<select id="p1-10-preview-state"><option value="">全部州代码</option></select></label><label>市代码<select id="p1-10-preview-municipality"><option value="">全部市代码</option></select></label>
    <label>Preview Snapshot<select id="p1-10-preview-snapshot" disabled></select></label><label>排序<select id="p1-10-preview-sort"><option value="date_asc">日期升序</option><option value="date_desc">日期降序</option><option value="value_desc">通知记录数降序</option></select></label>
    <button id="p1-10-preview-apply" type="button">应用筛选</button><button id="p1-10-preview-clear" type="button">清空筛选</button></section>
    <p id="p1-10-preview-status" class="notice" aria-live="polite">尚未读取候选 Preview。</p><section id="p1-10-preview-error" class="preview-error" hidden><h2>候选 Preview 暂不可用</h2><p>安全错误码：<code id="p1-10-preview-error-code"></code></p><p>request ID：<code id="p1-10-preview-request-id"></code></p><button id="p1-10-preview-retry" type="button">重试</button> <a href="http://127.0.0.1:8121/discoveries">返回 Source Review Console</a></section><section id="p1-10-preview-summary" class="preview-grid"></section>
    <section class="charts"><article><h2>图1：按通知日期的时间趋势</h2><div id="p1-10-preview-trend"></div></article><article><h2>图2：按通知地点州/市代码分布</h2><div id="p1-10-preview-geo"></div></article><article><h2>图3：质量与范围警告</h2><div id="p1-10-preview-quality" class="preview-quality"></div></article></section>
    <section class="card"><h2>候选 Preview 数据表</h2><div id="p1-10-preview-table" class="preview-table-wrap"></div><div class="preview-pagination"><button id="p1-10-preview-prev" type="button">上一页</button><span id="p1-10-preview-page">第 1/1 页</span><button id="p1-10-preview-next" type="button">下一页</button></div></section></section>""",
    1,
)
DASHBOARD_HTML = DASHBOARD_HTML.replace(
    "</head>",
    '<link rel="stylesheet" href="/static/dashboard.p1-8-daily.css"><link rel="stylesheet" href="/static/dashboard.p1-10-preview.css"></head>',
    1,
).replace(
    "</body>",
    '<script src="/static/dashboard.p1-8-daily.js" defer></script><script src="/static/dashboard.p1-10-preview.js" defer></script></body>',
    1,
)



# P1-17A prediction MVP routes
PREDICTIONS_HTML = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>流感监测高活动信号预测（工程原型）</title><link rel="stylesheet" href="/static/dashboard.p1-17a-predictions.css"></head><body class="prediction-page"><header><div><h1>流感监测高活动信号预测（工程原型）</h1><p class="warning">这是基于 FluNet 历史监测数据的工程链路演示，不是 WHO 官方预测，也不构成疫情预警或公共卫生决策依据。当前结果不是 Ebola 预测。页面区分：当前下一周预测、各序列最后可计算结果、回测结果。历史 vintage 可用性有限，LATEST 的实际标签显示为“待回填”。</p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><main><section class="filters"><label>Run<select id="runSelect"></select></label><label>结果范围<select id="scopeSelect"><option value="LATEST">当前下一周预测 / LATEST</option><option value="TEST">回测结果 / TEST</option></select></label><label><span>包含陈旧 LATEST</span><input type="checkbox" id="includeStale"></label><button id="reloadBtn" type="button">刷新</button></section><section id="status" class="card">加载中...</section><section id="runSummary" class="grid"></section><section class="card"><h2>风险时间曲线与 0.5 阈值线</h2><svg id="riskChart" role="img" aria-label="风险时间曲线"></svg></section><section class="card"><h2>最新 50 条 / 筛选结果</h2><div id="resultsTable"></div></section><section class="card"><h2>说明</h2><p>页面从 PostgreSQL API 读取 COMPLETE prediction run；不读取报告 CSV/JSON，不包含硬编码预测数组。TEST 指标名称使用 Average Precision (AP)。</p></section><script src="/static/dashboard.p1-17a-predictions.js" defer></script></main></body></html>'


# Fasttrack E2E forecast dashboard routes
FASTTRACK_FORECAST_HTML = "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>疫情预测闭环 · Fasttrack</title><link rel=\"stylesheet\" href=\"/static/dashboard.fasttrack-forecast.css\"></head><body><header><div><h1>疫情历史与未来 1–4 周预测</h1><p class=\"warn\">真实 PostgreSQL 数据 → 近期 rolling 回测 → 预测 → 80% 区间。缺失周不会补 0；本页面不是医疗或公共卫生决策建议。</p></div><form method=\"post\" action=\"/logout\"><button type=\"submit\">退出</button></form></header><main><section class=\"card filters\"><label>Run / 病种 / 来源<select id=\"runSelect\"></select></label><label>国家/地点<input id=\"locInput\" placeholder=\"输入或选择 location\"></label><label>地点选择<select id=\"locSelect\"></select></label><button id=\"reload\" type=\"button\">刷新</button><a id=\"csvLink\" href=\"#\">下载 CSV</a></section><section id=\"summary\" class=\"grid\"></section><section class=\"card\"><h2>图1：最近 2–3 年历史 + 未来预测区间</h2><svg id=\"chart\"></svg></section><section class=\"card\"><h2>未来 4 周数值表</h2><div id=\"future\"></div></section><section class=\"card\"><h2>图2：近期 rolling 回测实际值与预测值</h2><div id=\"backtest\"></div></section><section class=\"card\"><h2>图3：数据源、质量、新鲜度与模型说明</h2><div id=\"details\"></div></section></main><script src=\"/static/dashboard.fasttrack-forecast.js\" defer></script></body></html>"

def create_app() -> FastAPI:
    static_dir = Path(__file__).with_name("static")
    engine = create_engine(
        os.environ["DASHBOARD_DATABASE_URL"],
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=5000"},
        hide_parameters=True,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine, app.state.auth, app.state.sessions = (
            engine,
            AuthStore(Path(os.environ["DASHBOARD_AUTH_FILE"])),
            SessionStore(),
        )
        yield
        engine.dispose()

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return _json_response({"detail": "INVALID_REQUEST"}, 400)

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Response:
        rid = request_id(request)
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            response = unexpected_error(request, exc, review_console=False)
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "X-Request-ID": rid,
            }
        )
        return response

    @app.get("/healthz")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz")
    def ready() -> Response:
        payload, status = readiness(
            engine,
            expected_role="epidemic_dashboard_ro",
            required_views=(
                "epidemic_preview.active_preview_publications_v1",
                "epidemic_preview.preview_dashboard_series_v1",
                "epidemic_preview.preview_lineage_v1",
                "epidemic_ebola.active_preview_publications_v1",
                "epidemic_ebola.preview_series_v1",
                "epidemic_ebola.monitor_status_v1",
                "epidemic_p1_12.trusted_dashboard_series_v1",
                "epidemic_p1_12.trusted_dashboard_catalog_v1",
                "epidemic_p1_12.trusted_update_status_v1",
            ),
            forbidden_privileges=(
                ("table", "epidemic_discovery.candidate_fetches", "SELECT"),
                ("table", "epidemic_preview.preview_observations", "INSERT"),
                ("table", "epidemic_preview.preview_snapshots", "INSERT"),
                ("table", "epidemic.normalized_observations", "INSERT"),
            ),
        )
        return _json_response(payload, status)

    @app.get("/static/dashboard.p1-7ar-v1.css")
    def css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-7ar-v1.css", media_type="text/css")

    @app.get("/static/dashboard.p1-7ar-v1.js")
    def js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-7ar-v1.js", media_type="application/javascript")

    @app.get("/static/dashboard.p1-8-daily.css")
    def daily_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-8-daily.css", media_type="text/css")

    @app.get("/static/dashboard.p1-8-daily.js")
    def daily_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-8-daily.js", media_type="application/javascript")

    @app.get("/static/dashboard.p1-10-preview.css")
    def preview_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-10-preview.css", media_type="text/css")

    @app.get("/static/dashboard.p1-10-preview.js")
    def preview_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-10-preview.js", media_type="application/javascript")

    @app.get("/static/dashboard.p1-12-trusted.css")
    def p1_12_trusted_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-12-trusted.css", media_type="text/css")

    @app.get("/static/dashboard.p1-11-ebola.css")
    def ebola_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-11-ebola.css", media_type="text/css")


    @app.get("/static/dashboard.p1-17a-predictions.css")
    def p1_17a_prediction_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-17a-predictions.css", media_type="text/css")

    @app.get("/static/dashboard.p1-17a-predictions.js")
    def p1_17a_prediction_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-17a-predictions.js", media_type="application/javascript")

    @app.get("/static/dashboard.prediction-demo.js")
    def prediction_demo_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.prediction-demo.js", media_type="application/javascript")



    @app.get("/static/dashboard.legionellosis-preview.css")
    def legionellosis_preview_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.legionellosis-preview.css", media_type="text/css")

    @app.get("/static/dashboard.legionellosis-preview.js")
    def legionellosis_preview_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.legionellosis-preview.js", media_type="application/javascript")

    @app.get("/login")
    def login_page() -> HTMLResponse:
        return HTMLResponse(LOGIN_HTML.format(error=""))

    @app.post("/login")
    async def login(request: Request) -> Response:
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        username, password = str(values.get("username", [""])[0]), str(values.get("password", [""])[0])
        key = request.client.host if request.client else "local"
        if not request.app.state.sessions.allowed(key) or not request.app.state.auth.verify(username, password):
            request.app.state.sessions.failed(key)
            status = 429 if not request.app.state.sessions.allowed(key) else 401
            return HTMLResponse(LOGIN_HTML.format(error='<p class="error">登录失败。</p>'), status_code=status)
        request.app.state.sessions.succeeded(key)
        response = RedirectResponse("/", 303)
        response.set_cookie(
            COOKIE_NAME,
            request.app.state.sessions.create(),
            max_age=SESSION_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/logout")
    def logout(request: Request) -> Response:
        response = RedirectResponse("/login", 303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/")
    def dashboard(request: Request) -> Response:
        return HTMLResponse(DASHBOARD_HTML) if _authorized(request) else RedirectResponse("/login", 303)




    @app.get("/predictions")
    def predictions_page_p1_17a(request: Request) -> Response:
        return HTMLResponse(PREDICTIONS_HTML) if _authorized(request) else RedirectResponse("/login", 303)

    @app.get("/api/v1/predictions/runs")
    def prediction_runs_p1_17a(request: Request, limit: int = 20, offset: int = 0) -> Response:
        if not _authorized(request):
            return _unauthorized()
        if not 1 <= limit <= 100 or offset < 0:
            return _json_response({"detail": "INVALID_PAGINATION"}, 400)
        rows = _query(
            engine,
            "SELECT run_id,run_fingerprint_sha256,disease_slug,target_contract,horizon_weeks,dataset_sha256,split_sha256,model_sha256,feature_schema_sha256,model_config,feature_schema,label_spec,train_start,train_end,validation_start,validation_end,test_start,test_end,data_through,generated_at,completed_at,status,metrics,warnings,test_result_count,latest_result_count,test_rowset_sha256,latest_rowset_sha256 FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT :limit OFFSET :offset",
            {"limit": limit, "offset": offset},
        )
        total = _query(engine, "SELECT count(*) AS total FROM epidemic.prediction_runs_complete_v1")[0]["total"]
        return _json_response({"items": rows, "total": total, "limit": limit, "offset": offset})

    @app.get("/api/v1/predictions/runs/{run_id}")
    def prediction_run_detail_p1_17a(request: Request, run_id: uuid.UUID) -> Response:
        if not _authorized(request):
            return _unauthorized()
        rows = _query(engine, "SELECT * FROM epidemic.prediction_runs_complete_v1 WHERE run_id=:run_id", {"run_id": run_id})
        if not rows:
            return _json_response({"detail": "RUN_NOT_FOUND"}, 404)
        return _json_response(rows[0])

    @app.get("/api/v1/predictions/options")
    def prediction_options_p1_17a(request: Request, run_id: uuid.UUID | None = None) -> Response:
        if not _authorized(request):
            return _unauthorized()
        run_clause = "run_id=:run_id" if run_id else "run_id=(SELECT run_id FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT 1)"
        rows = _query(
            engine,
            f"SELECT country_iso3,country_name,region_id,surveillance_origin,scope,count(*) AS rows,min(target_week) AS min_target_week,max(target_week) AS max_target_week FROM epidemic.prediction_results_complete_v1 WHERE {run_clause} GROUP BY 1,2,3,4,5 ORDER BY country_iso3,region_id,surveillance_origin,scope",
            {"run_id": run_id},
        )
        return _json_response({"items": rows})

    @app.get("/api/v1/predictions/results")
    def prediction_results_p1_17a(
        request: Request,
        run_id: uuid.UUID | None = None,
        model: str | None = None,
        scope: str = "LATEST",
        include_stale: bool = False,
        country_iso3: str | None = None,
        region_id: str | None = None,
        surveillance_origin: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        minimum_risk: float | None = None,
        alert_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Response:
        if not _authorized(request):
            return _unauthorized()
        if scope not in {"TEST", "LATEST"} or not 1 <= limit <= 500 or offset < 0:
            return _json_response({"detail": "INVALID_REQUEST"}, 400)
        try:
            start, end = _strict_date(start_date), _strict_date(end_date)
        except ValueError:
            return _json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        params: dict[str, Any] = {"scope": scope, "limit": limit, "offset": offset}
        clauses = ["r.scope=:scope"]
        if run_id:
            clauses.append("r.run_id=:run_id")
            params["run_id"] = run_id
        else:
            clauses.append("r.run_id=(SELECT run_id FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT 1)")
        if model:
            clauses.append("r.model_key=:model")
            params["model"] = model
        if country_iso3:
            clauses.append("r.country_iso3=:country_iso3")
            params["country_iso3"] = country_iso3
        if region_id:
            clauses.append("r.region_id=:region_id")
            params["region_id"] = region_id
        if surveillance_origin:
            clauses.append("r.surveillance_origin=:surveillance_origin")
            params["surveillance_origin"] = surveillance_origin
        if start:
            clauses.append("r.target_week>=:start")
            params["start"] = start
        if end:
            clauses.append("r.target_week<=:end")
            params["end"] = end
        if minimum_risk is not None:
            if not 0 <= minimum_risk <= 1:
                return _json_response({"detail": "INVALID_RISK"}, 400)
            clauses.append("r.risk_score>=:minimum_risk")
            params["minimum_risk"] = minimum_risk
        if alert_only:
            clauses.append("r.predicted_alert=true")
        if scope == "LATEST" and not include_stale:
            clauses.append("r.target_week > pr.data_through")
        where = " AND ".join(clauses)
        base = "FROM epidemic.prediction_results_complete_v1 r JOIN epidemic.prediction_runs_complete_v1 pr ON pr.run_id=r.run_id"
        total = _query(engine, f"SELECT count(*) AS total {base} WHERE {where}", params)[0]["total"]
        rows = _query(
            engine,
            f"SELECT r.*, CASE WHEN r.target_week <= r.prediction_origin THEN 'INVALID' WHEN r.target_week > pr.data_through THEN 'FUTURE' WHEN r.target_week = pr.data_through THEN 'AT_WATERLINE' ELSE 'HISTORICAL' END AS freshness_status, pr.data_through AS source_data_through {base} WHERE {where} ORDER BY r.prediction_origin DESC, r.risk_score DESC, r.country_iso3 ASC, r.region_id ASC LIMIT :limit OFFSET :offset",
            params,
        )
        run_rows = _query(
            engine,
            "SELECT run_id,model_sha256,dataset_sha256,split_sha256,model_config,data_through,generated_at,completed_at,metrics,warnings FROM epidemic.prediction_runs_complete_v1 WHERE run_id=COALESCE(CAST(:run_id AS uuid),(SELECT run_id FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT 1))",
            {"run_id": run_id},
        )
        return _json_response({"items": rows, "total": total, "limit": limit, "offset": offset, "include_stale": include_stale, "order_by": "prediction_origin DESC, risk_score DESC, country_iso3 ASC, region_id ASC", "run": run_rows[0] if run_rows else None})


    @app.get("/api/v1/prediction-demo")
    def prediction_demo_api(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_historical_and_real_influenza_region_week_pipeline" / "prediction_dashboard_payload.json"
        if not payload_path.exists():
            payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_data_visualization_prediction_engineering_loop" / "prediction_dashboard_demo_payload.json"
            return _json_response({"detail": "PREDICTION_DEMO_NOT_GENERATED"}, 404)
        return _json_response(json.loads(payload_path.read_text(encoding="utf-8")))

    @app.get("/api/v1/options")
    def options_v1(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        rows = _query(
            engine, "SELECT * FROM epidemic.dashboard_catalog_options_v2 ORDER BY country_name_en,disease_name_en"
        )
        countries, diseases = {}, {}
        for row in rows:
            countries[row["country_iso2"]] = {
                key: row[key] for key in ("country_iso2", "country_iso3", "country_name_en", "country_name_zh")
            }
            diseases[row["disease_id"]] = {
                key: row[key] for key in ("disease_id", "disease_code", "disease_name_en", "disease_name_zh")
            }
        return _json_response({"countries": list(countries.values()), "diseases": list(diseases.values())})

    @app.get("/api/v1/snapshots")
    def snapshots_v1(request: Request, page: int = 1, page_size: int = 50) -> Response:
        if not _authorized(request):
            return _unauthorized()
        if page < 1 or not 1 <= page_size <= 100:
            return _json_response({"detail": "INVALID_PAGINATION"}, 400)
        total = _query(engine, "SELECT count(*) AS total FROM epidemic.dashboard_snapshot_catalog_v1")[0]["total"]
        items = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_snapshot_catalog_v1 ORDER BY source_data_through DESC NULLS LAST,snapshot_created_at DESC,snapshot_id LIMIT :limit OFFSET :offset",
            {"limit": page_size, "offset": (page - 1) * page_size},
        )
        return _json_response({"page": page, "page_size": page_size, "total": total, "items": items})

    @app.get("/api/v1/daily-updates")
    def daily_updates_v1(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        rows = _query(
            engine,
            "SELECT * FROM epidemic.daily_update_status_v1 ORDER BY source_key,country_alpha2,disease_slug",
        )
        return _json_response(
            {
                "items": rows,
                "timezone": "Asia/Shanghai",
                "definition": "Daily means check every day and publish only when approved source data changes.",
                "server_shutdown_limitation": True,
            }
        )

    @app.get("/api/v2/catalog/options")
    def catalog(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        rows = _query(
            engine, "SELECT * FROM epidemic.dashboard_catalog_options_v2 ORDER BY country_name_en,disease_name_en"
        )
        countries: dict[str, dict[str, Any]] = {}
        diseases: dict[str, dict[str, Any]] = {}
        for row in rows:
            country = countries.setdefault(
                row["country_iso2"],
                {key: row[key] for key in ("country_iso2", "country_iso3", "country_name_en", "country_name_zh")},
            )
            country.setdefault("data", []).append(
                {
                    key: row[key]
                    for key in (
                        "disease_id",
                        "disease_code",
                        "has_data",
                        "ready_snapshot_count",
                        "observation_count",
                        "data_start",
                        "data_end",
                        "source_data_through",
                    )
                }
            )
            diseases[row["disease_id"]] = {
                key: row[key] for key in ("disease_id", "disease_code", "disease_name_en", "disease_name_zh")
            }
        default = _query(
            engine,
            "SELECT snapshot_id,country_alpha2,disease_slug,source_key,source_data_through FROM epidemic.dashboard_snapshot_catalog_v1 ORDER BY source_data_through DESC NULLS LAST,snapshot_created_at DESC,snapshot_id LIMIT 1",
        )
        return _json_response(
            {
                "countries": list(countries.values()),
                "diseases": list(diseases.values()),
                "default_snapshot": default[0] if default else None,
                "static_version": STATIC_VERSION,
            }
        )

    @app.get("/api/v2/sources")
    def sources(request: Request, country_iso2: str, disease_id: uuid.UUID) -> Response:
        if not _authorized(request):
            return _unauthorized()
        rows = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_history_catalog_v2 WHERE country_alpha2=:country AND disease_id=:disease ORDER BY source_name,source_key",
            {"country": country_iso2, "disease": disease_id},
        )
        return _json_response({"items": rows})

    @app.get("/api/v2/history/metadata")
    def metadata(request: Request, country_iso2: str, disease_id: uuid.UUID, source_key: str = "all") -> Response:
        if not _authorized(request):
            return _unauthorized()
        clause = "" if source_key == "all" else " AND source_key=:source"
        rows = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_history_catalog_v2 WHERE country_alpha2=:country AND disease_id=:disease"
            + clause
            + " ORDER BY source_name,source_key",
            {"country": country_iso2, "disease": disease_id, "source": source_key},
        )
        return _json_response(
            {
                "items": rows,
                "snapshot_count": sum(int(x["ready_snapshot_count"]) for x in rows),
                "observation_count": sum(int(x["observation_count"]) for x in rows),
            }
        )

    @app.get("/api/v2/history/series")
    def history_series(
        request: Request,
        country_iso2: str,
        disease_id: uuid.UUID,
        source_key: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        cursor: str | None = None,
        page_size: int = 250,
    ) -> Response:
        if not _authorized(request):
            return _unauthorized()
        try:
            start, end = _strict_date(start_date), _strict_date(end_date)
        except ValueError:
            return _json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        if start and end and start > end:
            return _json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        if not 1 <= page_size <= 500 or (cursor is not None and not cursor.isdigit()):
            return _json_response({"detail": "INVALID_PAGINATION"}, 400)
        offset = int(cursor or 0)
        params = {
            "country": country_iso2,
            "disease": disease_id,
            "source": source_key,
            "start": start,
            "end": end,
            "limit": page_size + 1,
            "offset": offset,
        }
        source_clause = "" if source_key == "all" else " AND source_key=:source"
        rows = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_history_series_v2 WHERE country_alpha2=:country AND disease_id=:disease"
            + source_clause
            + " AND (CAST(:start AS date) IS NULL OR period_end>=CAST(:start AS date)) AND (CAST(:end AS date) IS NULL OR period_start<=CAST(:end AS date)) ORDER BY source_key,geographic_level,location_key,metric_code,granularity,period_start,period_end,observation_id LIMIT :limit OFFSET :offset",
            params,
        )
        more = len(rows) > page_size
        items = rows[:page_size]
        if offset + len(items) > MAX_POINTS:
            return _json_response({"detail": "POINT_LIMIT_EXCEEDED"}, 413)
        return _json_response(
            {
                "items": items,
                "page_size": page_size,
                "next_cursor": str(offset + page_size) if more else None,
                "complete": not more,
                "interpolated": False,
                "smoothed": False,
                "predicted": False,
            }
        )

    @app.get("/api/v1/snapshots/{snapshot_uuid}/series")
    def snapshot_series(
        request: Request, snapshot_uuid: uuid.UUID, start_date: str | None = None, end_date: str | None = None
    ) -> Response:
        if not _authorized(request):
            return _unauthorized()
        try:
            start, end = _strict_date(start_date), _strict_date(end_date)
        except ValueError:
            return _json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        if start and end and start > end:
            return _json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        catalog_rows = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_snapshot_catalog_v1 WHERE snapshot_id=:sid",
            {"sid": snapshot_uuid},
        )
        if not catalog_rows:
            return _json_response({"detail": "SNAPSHOT_NOT_FOUND"}, 404)
        params = {"sid": snapshot_uuid, "start": start, "end": end}
        where = "snapshot_id=:sid AND (CAST(:start AS date) IS NULL OR period_end>=CAST(:start AS date)) AND (CAST(:end AS date) IS NULL OR period_start<=CAST(:end AS date))"
        points = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_observation_series_v1 WHERE "
            + where
            + " ORDER BY period_start,granularity,metric_code,revision",
            params,
        )
        quality = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_quality_summary_v1 WHERE " + where + " ORDER BY period_start,granularity",
            params,
        )
        return _json_response(
            {
                "snapshot": catalog_rows[0],
                "points": points,
                "quality": quality,
                "point_count": len(points),
                "resampled": False,
                "interpolated": False,
                "predicted": False,
            }
        )



    @app.get("/legionellosis-preview")
    def legionellosis_preview_page(request: Request) -> Response:
        if not _authorized(request):
            return RedirectResponse("/login", 303)
        html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>美国军团菌病 CDC 周度 Preview</title><link rel="stylesheet" href="/static/dashboard.legionellosis-preview.css"></head><body class="legionellosis-preview"><header><div><h1>美国 Legionellosis / 军团菌病周度 Preview</h1><p>CDC NNDSS Weekly Tables · Current week · preview/staging only</p><p><a href="/">返回主 Dashboard</a> · <a id="csvLink" href="/api/v1/legionellosis-preview.csv">下载 CSV</a></p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><main><section id="notice" class="warning">预览数据：仅展示已验证周，尚未形成完整104周序列。</section><section id="status" class="notice" aria-live="polite">初始化…</section><section id="summary" class="summary" aria-label="覆盖摘要"></section><section class="card"><h2>周度 Current week 病例折线图</h2><p>缺失周和待验证周显示为缺口或底部标记；禁止补零和插值。</p><svg id="chart" role="img" aria-label="美国军团菌病 Current week preview 折线图"></svg></section><section id="gaps" class="card"></section><section class="card"><h2>已验证周数据表</h2><div id="table"></div></section></main><script src="/static/dashboard.legionellosis-preview.js" defer></script></body></html>"""
        return HTMLResponse(html)

    def _legionellosis_preview_payload_path() -> Path:
        return Path(__file__).resolve().parents[3] / "storage" / "preview" / "us_legionellosis" / "preview_us_legionellosis_weekly_current_week_v1.json"

    @app.get("/api/v1/legionellosis-preview")
    def legionellosis_preview_api(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        payload_path = _legionellosis_preview_payload_path()
        if not payload_path.exists():
            return _json_response({"detail": "LEGIONELLOSIS_PREVIEW_NOT_GENERATED"}, 404)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        return _json_response(payload)

    @app.get("/api/v1/legionellosis-preview.csv")
    def legionellosis_preview_csv(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        csv_path = _legionellosis_preview_payload_path().with_suffix(".csv")
        if not csv_path.exists():
            return _json_response({"detail": "LEGIONELLOSIS_PREVIEW_NOT_GENERATED"}, 404)
        return FileResponse(csv_path, media_type="text/csv", filename="preview_us_legionellosis_weekly_current_week_v1.csv")

    install_preview_routes(app, engine, _authorized, _unauthorized, _query, _json_response)
    install_ebola_routes(app, engine, _authorized, _unauthorized, _query, _json_response)
    install_p1_12_routes(app, engine, _authorized, _unauthorized, _query, _json_response)
    install_forecast_model_kg_routes(app, engine, _authorized, _unauthorized, _json_response)
    install_kg_p1_routes(app, engine, _authorized, _unauthorized, _json_response)
    install_kg_r3c_routes(app, engine, _authorized, _unauthorized, _json_response)
    install_dual_loop_routes(app, engine, _authorized, _unauthorized, _json_response)
    install_pt_routes(app, engine, _authorized, _unauthorized, _json_response)





    @app.get("/static/dashboard.fasttrack-forecast.css")
    def fasttrack_forecast_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.fasttrack-forecast.css", media_type="text/css")

    @app.get("/static/dashboard.fasttrack-forecast.js")
    def fasttrack_forecast_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.fasttrack-forecast.js", media_type="application/javascript")

    @app.get("/fasttrack-forecast")
    def fasttrack_forecast_page(request: Request) -> Response:
        if "_authorized" in globals() and not _authorized(request):
            return RedirectResponse("/login", 303)
        return HTMLResponse(FASTTRACK_FORECAST_HTML)

    @app.get("/api/v1/fasttrack-forecast/runs")
    def fasttrack_forecast_runs(request: Request) -> Response:
        if "_authorized" in globals() and not _authorized(request):
            return _unauthorized()
        rows = _query(engine, "SELECT id::text,run_key,disease_slug,target_metric,target_label,source_key,status,validation_level,cutoff_week::text,horizons,dataset_sha256,git_sha,champion_model,fallback_reason,published_at::text,retraction_reason FROM epidemic.forecast_runs WHERE status IN ('PUBLISHED','INVALIDATED') ORDER BY CASE WHEN status='PUBLISHED' AND target_metric='cases_new' AND validation_level='PRODUCTION_VALIDATED' THEN 0 WHEN status='PUBLISHED' AND target_metric='cases_new' THEN 1 WHEN status='PUBLISHED' THEN 2 ELSE 9 END, published_at DESC NULLS LAST, created_at DESC LIMIT 50")
        default_rows = _query(engine, "SELECT id::text FROM epidemic.forecast_runs WHERE status='PUBLISHED' ORDER BY CASE WHEN target_metric='cases_new' AND validation_level='PRODUCTION_VALIDATED' THEN 0 WHEN target_metric='cases_new' THEN 1 ELSE 2 END, published_at DESC NULLS LAST, created_at DESC LIMIT 1")
        return _json_response({"runs": rows, "default_run_id": default_rows[0]["id"] if default_rows else None})

    @app.get("/api/v1/fasttrack-forecast/summary")
    def fasttrack_forecast_summary(request: Request, run_id: str | None = None) -> Response:
        if "_authorized" in globals() and not _authorized(request):
            return _unauthorized()
        if not run_id:
            return _json_response({"detail":"RUN_ID_REQUIRED"}, 422)
        rows = _query(engine, "SELECT id::text,run_key,disease_slug,target_metric,target_label,source_key,status,validation_level,cutoff_week::text,horizons,dataset_sha256,git_sha,model_name,champion_model,fallback_reason,metrics,data_summary,gpu_evidence,threshold_method,alert_threshold::float,predicted_alert_week::text,published_at::text,retraction_reason FROM epidemic.forecast_runs WHERE id=CAST(:run AS uuid)", {"run": run_id})
        if not rows:
            return _json_response({"detail":"FORECAST_RUN_NOT_FOUND"}, 404)
        run = rows[0]
        if run["status"] != "PUBLISHED":
            return _json_response({"run": run, "locations": [], "default_location": None, "metrics": run.get("metrics") or {}, "details": {"状态": "该 run 已撤回，仅供审计查询", "原因": run.get("retraction_reason")}}, 200)
        locs = _query(engine, "SELECT location_id,max(location_name) AS location_name,count(*) AS rows,max(data_through_week)::text AS data_through_week,max(reporting_lag_weeks) AS reporting_lag_weeks FROM epidemic.forecast_values WHERE forecast_run_id=CAST(:run AS uuid) GROUP BY location_id ORDER BY rows DESC, location_id", {"run": run_id})
        summary = run.get("data_summary") or {}
        details = {"数据源": run.get("source_key"), "dataset SHA": run.get("dataset_sha256"), "Git SHA": run.get("git_sha"), "data through": summary.get("data_through_week") or run.get("cutoff_week"), "fresh/stale": summary.get("freshness"), "fallback reason": run.get("fallback_reason"), "预警阈值": run.get("alert_threshold"), "predicted_alert_week": run.get("predicted_alert_week"), "说明": "工程性预测，不是医疗或公共卫生决策建议"}
        return _json_response({"run": run, "locations": locs, "default_location": locs[0]["location_id"] if locs else None, "metrics": run.get("metrics") or {}, "details": details})

    @app.get("/api/v1/fasttrack-forecast/series")
    def fasttrack_forecast_series(request: Request, run_id: str | None = None, location_id: str | None = None) -> Response:
        if "_authorized" in globals() and not _authorized(request):
            return _unauthorized()
        if not run_id:
            return _json_response({"detail":"RUN_ID_REQUIRED"}, 422)
        if not location_id:
            loc_rows = _query(engine, "SELECT location_id,count(*) n FROM epidemic.forecast_values WHERE forecast_run_id=CAST(:run AS uuid) GROUP BY 1 ORDER BY n DESC LIMIT 1", {"run": run_id})
            location_id = loc_rows[0]["location_id"] if loc_rows else ""
        history = _query(engine, "SELECT location_id,location_name,country_iso3,week::text,actual::float,metric_code,source_key FROM epidemic.forecast_history_points WHERE forecast_run_id=CAST(:run AS uuid) AND location_id=:loc ORDER BY week", {"run": run_id, "loc": location_id})
        forecast = _query(engine, "SELECT location_id,location_name,country_iso3,issue_week::text,target_week::text,horizon,kind,model_name,yhat::float,lower_80::float,upper_80::float,actual::float,data_through_week::text,feature_max_week::text,reporting_lag_weeks,last_observed_value::float,actual_observed,feature_sha256,prediction_sha256 FROM epidemic.forecast_values WHERE forecast_run_id=CAST(:run AS uuid) AND location_id=:loc ORDER BY kind,target_week,horizon", {"run": run_id, "loc": location_id})
        return _json_response({"history": history, "forecast": forecast, "location_id": location_id, "run_id": run_id})

    @app.get("/api/v1/fasttrack-forecast/csv")
    def fasttrack_forecast_csv(request: Request, run_id: str | None = None) -> Response:
        if "_authorized" in globals() and not _authorized(request):
            return _unauthorized()
        if not run_id:
            return _json_response({"detail":"RUN_ID_REQUIRED"}, 422)
        rows = _query(engine, "SELECT v.location_id,v.location_name,v.country_iso3,v.issue_week::text,v.target_week::text,v.horizon,v.kind,v.model_name,v.yhat::float,v.lower_80::float,v.upper_80::float,v.actual::float,v.data_through_week::text,v.feature_max_week::text,v.reporting_lag_weeks,v.last_observed_value::float,v.actual_observed,v.feature_sha256,v.prediction_sha256 FROM epidemic.forecast_runs r JOIN epidemic.forecast_values v ON v.forecast_run_id=r.id WHERE r.id=CAST(:run AS uuid) AND r.status='PUBLISHED' ORDER BY v.location_id,v.kind,v.target_week,v.horizon", {"run": run_id})
        buf = io.StringIO()
        fields = ["location_id","location_name","country_iso3","issue_week","target_week","horizon","kind","model_name","yhat","lower_80","upper_80","actual","data_through_week","feature_max_week","reporting_lag_weeks","last_observed_value","actual_observed","feature_sha256","prediction_sha256"]
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=fasttrack_forecast.csv"})

    return app

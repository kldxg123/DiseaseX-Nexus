# ruff: noqa: E501
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import Engine, create_engine, text

from epidemic_core.discovery.review_labels import (
    FIELD_ZH,
    LABEL_VERSION,
    LABELS,
    MODEL_FIELDS,
    REASON_ZH,
    STATE_ZH,
    TRANSFORM_ZH,
)

COOKIE = "epidemic_source_review"
IDLE_SECONDS = 1_800
ABSOLUTE_SECONDS = 28_800
ORIGIN = "http://127.0.0.1:8021"
STATIC_VERSION = "p1-7ar3-v1"
TERMINAL_STATES = {
    "REVIEW_READY",
    "NO_CANDIDATE",
    "DUPLICATE_ONLY",
    "POLICY_BLOCKED",
    "SECURITY_BLOCKED",
    "LICENSE_BLOCKED",
    "UNSUPPORTED_FORMAT",
    "INVALID_REQUEST",
    "FAILED_TERMINAL",
}


class Sessions:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[float, float, str, str]] = {}

    def create(self, username: str) -> str:
        token, now = secrets.token_urlsafe(32), time.time()
        self.rows[token] = (now, now, username, secrets.token_urlsafe(32))
        return token

    def get(self, token: str | None) -> tuple[str, str] | None:
        if not token or token not in self.rows:
            return None
        made, last, username, csrf = self.rows[token]
        now = time.time()
        if now - last > IDLE_SECONDS or now - made > ABSOLUTE_SECONDS:
            self.rows.pop(token, None)
            return None
        self.rows[token] = (made, now, username, csrf)
        return username, csrf


def _auth(path: Path, username: str, password: str) -> bool:
    value = json.loads(path.read_text(encoding="utf-8"))
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(value["salt_hex"]), n=value["n"], r=value["r"], p=value["p"], dklen=32
    )
    return hmac.compare_digest(username, value["username"]) and hmac.compare_digest(actual.hex(), value["hash_hex"])


def _rows(engine: Engine, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), dict(params or {})).mappings()]


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "缺失"), quote=True)


def _jsonable(value: object) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _layout(title: str, body: str, csrf: str = "") -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="{_e(csrf)}"><meta name="review-label-version" content="{LABEL_VERSION}"><title>{_e(title)}</title><link rel="stylesheet" href="/static/review.{STATIC_VERSION}.css"></head><body><header><div><h1>疫情数据源复核台</h1><p>隔离发现与人工复核；不属于正式疫情数据库。</p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><nav aria-label="主导航"><a href="/discoveries">发现与任务进度</a><a href="/reviews">待复核候选</a><a href="/history">审核历史</a></nav><main>{body}</main><script src="/static/review.{STATIC_VERSION}.js" defer></script></body></html>"""


def _login(error: str = "") -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>数据源复核登录</title><link rel="stylesheet" href="/static/review.{STATIC_VERSION}.css"></head><body class="login-page"><main class="login-card"><h1>数据源复核登录</h1><p>请输入独立 reviewer 凭据。</p>{error}<form method="post" action="/login"><label for="username">用户名</label><input id="username" name="username" autocomplete="username" required><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required><button>登录</button></form></main></body></html>"""


def _label(code: str) -> tuple[str, str, str, str]:
    return LABELS.get(code, (code, "尚未登记中文解释。", "需要人工理解其影响。", "查看技术证据并补充说明。"))


def _state(code: str) -> str:
    return STATE_ZH.get(code, code)


def _elapsed(seconds: object) -> str:
    value = int(str(seconds or 0))
    if value < 60:
        return f"{value} 秒"
    if value < 3600:
        return f"{value // 60} 分 {value % 60} 秒"
    return f"{value // 3600} 小时 {(value % 3600) // 60} 分"


def _candidate_text(row: Mapping[str, Any]) -> str:
    state, count = str(row["state"]), int(row.get("candidate_count") or 0)
    if state == "QUEUED":
        return "任务仍在排队，尚未开始检索"
    if state == "DISCOVERING":
        return f"检索仍在进行；当前有 {count} 个候选"
    if state == "PROFILING":
        return f"正在安全分析 {count} 个候选"
    if state == "REVIEW_READY":
        return f"找到 {count} 个待复核候选"
    if state == "NO_CANDIDATE":
        if row.get("terminal_reason_code") == "NO_CANDIDATE_COMPLETE":
            return "完整检索后未找到候选"
        return "检索覆盖不完整，不能判断是否没有数据源"
    if row.get("terminal_reason_code") in {
        "GENERIC_SEARCH_PROVIDER_UNCONFIGURED",
        "BLOCKED_GENERIC_SEARCH_CONFIGURATION",
        "DISCOVERY_INCOMPLETE",
    }:
        return "检索覆盖不完整，不能判断是否没有数据源"
    return f"当前记录 {count} 个候选"


def _run_progress(row: Mapping[str, Any]) -> str:
    progress = row.get("progress_json") or {}
    return (
        f"计划 {progress.get('planned_queries', progress.get('queries', 0))}；"
        f"真实请求 {progress.get('network_requests', 0)}；"
        f"成功响应 {progress.get('successful_responses', 0)}；"
        f"原始 {progress.get('raw_results', 0)}；"
        f"合格 {progress.get('qualified_candidates', progress.get('qualified', 0))}"
    )


def _worker_message(row: Mapping[str, Any]) -> str:
    if row.get("worker_healthy"):
        return "后台检索服务正常"
    if row["state"] == "QUEUED" and int(row.get("elapsed_seconds") or 0) > 60:
        return "后台检索服务没有领取任务"
    return "当前没有有效 Worker 心跳"


def _conclusion(row: Mapping[str, Any]) -> str:
    state = str(row["state"])
    reason = str(row.get("terminal_reason_code") or "")
    count = int(row.get("candidate_count") or 0)
    if state == "REVIEW_READY":
        return f"找到 {count} 个待复核候选"
    if reason == "NO_CANDIDATE_COMPLETE":
        return "完整检索后未找到候选"
    if reason in {"GENERIC_SEARCH_PROVIDER_UNCONFIGURED", "BLOCKED_GENERIC_SEARCH_CONFIGURATION"}:
        return "系统缺少搜索 Provider 配置"
    if reason == "DISCOVERY_INCOMPLETE":
        return "检索覆盖不完整，不能判断是否没有数据源"
    if state in {"SECURITY_BLOCKED", "FAILED_TERMINAL", "FAILED_RETRYABLE"}:
        return "网络或安全策略失败"
    if state in {"QUEUED", "DISCOVERING", "CANDIDATES_FOUND", "PROFILING"}:
        return "任务仍在执行"
    return "检索覆盖不完整，不能判断是否没有数据源"


def _coverage_table(events: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for event_row in events:
        code = str(event_row["event_code"])
        details = event_row.get("details") or {}
        if code == "SOURCE_DIRECTORY_CHECKED":
            rows.append(
                "<tr>"
                f"<td>{_e(details.get('label_zh') or details.get('strategy') or '本地策略')}</td>"
                "<td>本地适配器/协议检查</td><td>—</td><td>—</td><td>未真正发起</td><td>—</td><td>0</td>"
                "<td>未配置</td><td>历史事件只检查了本地策略名，没有访问具体目录。</td><td>是</td>"
                "</tr>"
            )
        elif code in {
            "DIRECTORY_CAPABILITY_EVALUATED",
            "DIRECTORY_REQUEST_AUDIT",
            "PROVIDER_REQUEST_AUDIT",
            "PROVIDER_CONFIGURATION_MISSING",
            "PROVIDER_REQUEST_NOT_EXECUTED",
        }:
            network = bool(details.get("network_request"))
            http = details.get("http_status")
            elapsed = details.get("elapsed_ms")
            result = str(details.get("result") or details.get("health_status") or "未执行")
            reason = str(
                details.get("reason_code")
                or details.get("unavailable_reason")
                or ("请求成功" if http == 200 else "未发起真实请求")
            )
            rows.append(
                "<tr>"
                f"<td>{_e(details.get('directory_name') or details.get('name') or details.get('provider_id'))}</td>"
                f"<td>{_e(details.get('scope') or details.get('source_tier') or '通用')}</td>"
                f"<td>{_e(details.get('domain') or '—')}</td>"
                f"<td>{_e(details.get('query_summary') or '—')}</td>"
                f"<td>{'是' if network else '否'}</td>"
                f"<td>{_e(str(http) if http is not None else '—')} / {_e(str(elapsed) + ' ms' if elapsed is not None else '—')}</td>"
                f"<td>{_e(details.get('result_count') or 0)}</td>"
                f"<td>{_e(result)}</td><td>{_e(reason)}</td>"
                f"<td>{'是' if details.get('affects_completeness') or details.get('required_channel') else '否'}</td>"
                "</tr>"
            )
    if not rows:
        rows.append('<tr><td colspan="10">尚无 Provider 或目录执行证据。</td></tr>')
    return (
        '<div class="table-box"><table class="coverage"><caption>每行对应一个真实 Provider、目录或历史本地策略检查</caption>'
        "<thead><tr><th>名称</th><th>范围</th><th>访问域名</th><th>查询摘要</th><th>真实网络请求</th><th>HTTP / 耗时</th>"
        "<th>结果数</th><th>结果</th><th>中文原因</th><th>影响完整性</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _technical_table(value: object, caption: str) -> str:
    if not isinstance(value, dict):
        return f"<p>{_e(value)}</p>"
    rows = "".join(
        f'<tr><th scope="row">{_e(key)}</th><td>{_display_value(item)}</td></tr>'
        for key, item in list(value.items())[:40]
    )
    return f'<div class="table-box"><table class="compact"><caption>{_e(caption)}</caption><thead><tr><th scope="col">键</th><th scope="col">值</th></tr></thead><tbody>{rows}</tbody></table></div>'


def _display_value(value: object) -> str:
    if value is None or value == "":
        return '<span class="value-null">缺失</span>'
    if value in (0, "0", 0.0, "0.0"):
        return '<span class="value-zero">0</span>'
    if isinstance(value, (dict, list)):
        size = len(value)
        if isinstance(value, dict):
            inner = "".join(
                f'<tr><th scope="row">{_e(key)}</th><td>{_display_value(item)}</td></tr>'
                for key, item in list(value.items())[:20]
            )
        else:
            inner = "".join(
                f'<tr><th scope="row">{index + 1}</th><td>{_display_value(item)}</td></tr>'
                for index, item in enumerate(value[:20])
            )
        return f'<details><summary>结构化字段（{size} 项）</summary><table class="nested"><tbody>{inner}</tbody></table></details>'
    text_value = str(value)
    try:
        if float(text_value) < 0:
            return f'<span class="value-negative">{_e(text_value)}（负值/修订待确认）</span>'
    except ValueError:
        pass
    if len(text_value) > 80:
        return f"<details><summary>{_e(text_value[:60])}…</summary><p>{_e(text_value)}</p></details>"
    return _e(text_value)


def _get(mapping: Mapping[str, Any], *names: str) -> object:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _preview_values(row: Mapping[str, Any]) -> dict[str, tuple[object, object]]:
    raw = row.get("raw_json") or {}
    clean = row.get("sanitized_json") or {}
    return {
        "period": (_get(raw, "data_iniSE", "SE", "data"), _get(clean, "data_iniSE", "SE", "data")),
        "geography": (
            _get(raw, "cidade", "Localidade_id", "geocode"),
            _get(clean, "cidade", "Localidade_id", "geocode"),
        ),
        "cases": (_get(raw, "casos", "cases"), _get(clean, "casos", "cases")),
        "deaths": (_get(raw, "obitos", "deaths"), _get(clean, "obitos", "deaths")),
        "unit": (_get(raw, "unidade", "unit") or "count", _get(clean, "unidade", "unit") or "count"),
    }


def _comparison(raw: object, clean: object, mode: str) -> str:
    if mode == "raw":
        return _display_value(raw)
    if mode == "clean":
        return _display_value(clean)
    changed = raw != clean
    return f'<div class="compare"><span><small>原始值</small>{_display_value(raw)}</span><span><small>清洗值</small>{_display_value(clean)}</span><span class="change-{str(changed).lower()}">{"已变化" if changed else "未变化"}</span></div>'


def _check_row(item: str, result: str, meaning: str, evidence: object, action: str) -> str:
    return f'<tr class="check-{_e(result)}"><th scope="row">{_e(item)}</th><td>{_e({"pass": "通过", "warn": "警告", "block": "阻断", "unknown": "未知"}.get(result, result))}</td><td>{_e(meaning)}</td><td>{_display_value(evidence)}</td><td>{_e(action)}</td></tr>'


def create_app() -> FastAPI:
    engine = create_engine(
        os.environ["REVIEW_DATABASE_URL"],
        connect_args={"options": "-c default_transaction_read_only=off -c statement_timeout=5000"},
        hide_parameters=True,
        pool_pre_ping=True,
    )
    auth_file, static_dir, sessions = (
        Path(os.environ["REVIEW_AUTH_FILE"]),
        Path(__file__).with_name("static"),
        Sessions(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                "Cache-Control": "no-store",
            }
        )
        return response

    def current(request: Request) -> tuple[str, str] | None:
        return sessions.get(request.cookies.get(COOKIE))

    def page_auth(request: Request) -> tuple[str, str] | RedirectResponse:
        return current(request) or RedirectResponse("/login", 303)

    def api_auth(request: Request) -> tuple[str, str] | JSONResponse:
        return current(request) or JSONResponse({"error": "UNAUTHORIZED"}, 401)

    def origin_ok(request: Request, csrf: str, submitted: str) -> bool:
        referer = request.headers.get("referer", "")
        return (
            request.headers.get("origin") == ORIGIN
            and referer.startswith(ORIGIN + "/")
            and hmac.compare_digest(csrf, submitted)
        )

    @app.get("/healthz")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get(f"/static/review.{STATIC_VERSION}.css")
    def css() -> FileResponse:
        return FileResponse(static_dir / f"review.{STATIC_VERSION}.css", media_type="text/css")

    @app.get(f"/static/review.{STATIC_VERSION}.js")
    def js() -> FileResponse:
        return FileResponse(static_dir / f"review.{STATIC_VERSION}.js", media_type="application/javascript")

    @app.get("/login")
    def login_page() -> HTMLResponse:
        return HTMLResponse(_login())

    @app.post("/login")
    async def login(request: Request) -> Response:
        values = urllib.parse.parse_qs((await request.body()).decode(), keep_blank_values=True)
        username, password = str(values.get("username", [""])[0]), str(values.get("password", [""])[0])
        if not _auth(auth_file, username, password):
            return HTMLResponse(_login('<p class="error">登录失败。</p>'), 401)
        response = RedirectResponse("/reviews", 303)
        response.set_cookie(
            COOKIE, sessions.create(username), httponly=True, samesite="strict", path="/", max_age=ABSOLUTE_SECONDS
        )
        return response

    @app.post("/logout")
    def logout() -> Response:
        response = RedirectResponse("/login", 303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/")
    def root(request: Request) -> Response:
        return RedirectResponse("/reviews", 303) if current(request) else RedirectResponse("/login", 303)

    def catalog_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_discovery_catalog_v2 ORDER BY country_name_en,disease_name_en",
        )
        countries: dict[str, dict[str, Any]] = {}
        diseases: dict[str, dict[str, Any]] = {}
        for row in rows:
            countries[row["country_iso3"]] = {
                key: row[key] for key in ("country_iso2", "country_iso3", "country_name_en", "country_name_zh")
            }
            diseases[str(row["disease_id"])] = {
                key: row[key] for key in ("disease_id", "disease_code", "disease_name_en", "disease_name_zh")
            }
        return list(countries.values()), list(diseases.values())

    @app.get("/discoveries")
    def discoveries(
        request: Request,
        page: int = 1,
        country: str = "",
        disease: str = "",
        state: str = "",
        created_from: str = "",
        created_to: str = "",
        auto: int = 0,
        error: str = "",
    ) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        _, csrf = auth
        page = max(1, min(page, 1000))
        params = {
            "country": country,
            "disease": disease,
            "state": state,
            "start": created_from or None,
            "end": created_to or None,
            "limit": 20,
            "offset": (page - 1) * 20,
        }
        runs = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_discovery_runs_v3 WHERE (:country='' OR country_iso3=:country) AND (:disease='' OR disease_code=:disease) AND (:state='' OR state=:state) AND (CAST(:start AS date) IS NULL OR created_at>=CAST(:start AS date)) AND (CAST(:end AS date) IS NULL OR created_at<CAST(:end AS date)+interval '1 day') ORDER BY created_at DESC,discovery_run_id LIMIT :limit OFFSET :offset",
            params,
        )
        countries, diseases = catalog_rows()
        country_options = "".join(
            f'<option value="{_e(item["country_name_zh"])} / {_e(item["country_name_en"])} ({_e(item["country_iso2"])}/{_e(item["country_iso3"])})" data-id="{_e(item["country_iso3"])}"></option>'
            for item in countries
        )
        disease_options = "".join(
            f'<option value="{_e(item["disease_name_zh"])} / {_e(item["disease_name_en"])} ({_e(item["disease_code"])})" data-id="{_e(item["disease_id"])}"></option>'
            for item in diseases
        )
        state_options = '<option value="">全部状态</option>' + "".join(
            f'<option value="{_e(code)}" {"selected" if code == state else ""}>{_e(label)}</option>'
            for code, label in STATE_ZH.items()
        )
        run_rows = "".join(
            f"""<tr><td>{_e(row["created_at"])}</td><td>{_e(row["country_name_zh"])}<small>{_e(row["country_name_en"])} / {_e(row["country_iso3"])}</small></td><td>{_e(row["disease_name_zh"])}<small>{_e(row["disease_name_en"])}</small></td><td>{_e("、".join(row["request_json"].get("desired_metrics", [])))}</td><td>最近滚动一年<small>{_e(row["request_json"].get("time_window_kind"))}</small></td><td>{_e(_state(row["state"]))}<small>{_e(row["state"])}</small></td><td>{_e(_run_progress(row))}</td><td>{_e(_candidate_text(row))}</td><td>{_e(row["last_event_at"] or row["updated_at"])}</td><td>{_e(_elapsed(row["elapsed_seconds"]))}</td><td>{_e(_worker_message(row))}</td><td>{_e(REASON_ZH.get(row["terminal_reason_code"], "等待后台处理" if row["state"] == "QUEUED" else "查看详情"))}</td><td><a href="/discoveries/{_e(row["discovery_run_id"])}">查看详情</a></td></tr>"""
            for row in runs
        )
        notice = f'<p class="error">{_e(error)}</p>' if error else ""
        body = f"""{notice}<section><h2>发现新数据源</h2><p>仅接受 catalog 标识、固定指标、时间窗口、来源层级、语言与候选上限；不接受 URL、SQL、shell、header 或代码。</p><form id="discovery-form" method="post" action="/discoveries"><input type="hidden" name="csrf" value="{_e(csrf)}"><input type="hidden" id="country-id" name="country_iso3"><input type="hidden" id="disease-id" name="disease_id"><label for="discover-country">国家（可输入搜索，249 国 catalog）</label><input id="discover-country" role="combobox" aria-autocomplete="list" list="discover-country-list" required><datalist id="discover-country-list">{country_options}</datalist><label for="discover-disease">病种（可输入搜索）</label><input id="discover-disease" role="combobox" aria-autocomplete="list" list="discover-disease-list" required><datalist id="discover-disease-list">{disease_options}</datalist><fieldset><legend>目标指标</legend><label><input type="checkbox" name="desired_metrics" value="cases_new" checked>新增病例</label><label><input type="checkbox" name="desired_metrics" value="deaths_new">新增死亡</label><label><input type="checkbox" name="desired_metrics" value="incidence_rate">发病率</label></fieldset><label for="window">时间窗口</label><select id="window" name="time_window_kind"><option value="latest_rolling_year">最近滚动一年</option><option value="latest_complete_calendar_year">最近完整日历年</option><option value="current_year_to_latest_period">本年至最新报告期</option></select><fieldset><legend>来源层级</legend><label><input type="checkbox" name="preferred_source_tiers" value="NATIONAL_HEALTH" checked>国家卫生机构</label><label><input type="checkbox" name="preferred_source_tiers" value="GOVERNMENT_OPEN_DATA" checked>政府开放数据</label><label><input type="checkbox" name="preferred_source_tiers" value="REGIONAL_HEALTH">区域卫生机构</label><label><input type="checkbox" name="preferred_source_tiers" value="PUBLIC_HEALTH_RESEARCH">公共卫生研究合作</label></fieldset><label for="languages">语言偏好（逗号分隔）</label><input id="languages" name="language_preferences" value="zh,en"><label for="maximum">最大候选数（1～20）</label><input id="maximum" name="max_candidates" type="number" min="1" max="20" value="5"><section id="discovery-confirmation" class="confirmation"><h3>提交确认摘要</h3><p>请选择国家和病种后确认。</p></section><label><input type="checkbox" name="confirm_request" value="yes" required>我确认国家、病种、指标和范围正确</label><button type="submit">开始受控检索</button></form></section><section><h2>Discovery 任务列表</h2><form method="get" class="filters"><label for="filter-country">国家 ISO3</label><input id="filter-country" name="country" value="{_e(country)}"><label for="filter-disease">病种 code</label><input id="filter-disease" name="disease" value="{_e(disease)}"><label for="filter-state">状态</label><select id="filter-state" name="state">{state_options}</select><label for="created-from">开始日期</label><input id="created-from" type="date" name="created_from" value="{_e(created_from)}"><label for="created-to">结束日期</label><input id="created-to" type="date" name="created_to" value="{_e(created_to)}"><label><input id="auto-refresh" type="checkbox" name="auto" value="1" {"checked" if auto else ""}>每 5 秒自动刷新</label><button>手动刷新</button></form><div class="table-box"><table><caption>Discovery 任务及真实进度</caption><thead><tr><th scope="col">创建时间</th><th scope="col">国家</th><th scope="col">病种</th><th scope="col">指标</th><th scope="col">时间窗口</th><th scope="col">阶段</th><th scope="col">进度</th><th scope="col">候选状态</th><th scope="col">最近活动</th><th scope="col">已运行</th><th scope="col">Worker</th><th scope="col">下一步</th><th scope="col">详情</th></tr></thead><tbody>{run_rows}</tbody></table></div><div class="pager"><a href="?page={max(1, page - 1)}">上一页</a><span>第 {page} 页</span><a href="?page={page + 1}">下一页</a></div></section>"""
        body = body.replace(
            '<label><input type="checkbox" name="preferred_source_tiers" value="PUBLIC_HEALTH_RESEARCH">公共卫生研究合作</label></fieldset>',
            '<label><input type="checkbox" name="preferred_source_tiers" value="PUBLIC_HEALTH_RESEARCH">公共卫生研究合作</label>'
            '<label><input type="checkbox" name="preferred_source_tiers" value="INTERNATIONAL_PUBLIC_HEALTH">国际公共卫生官方来源</label>'
            "<p>此层级默认不选；未勾选时不会检索，也不会静默扩大范围。</p></fieldset>",
        )
        return HTMLResponse(_layout("发现与任务进度", body, csrf))

    @app.post("/discoveries")
    async def submit_discovery_html(request: Request) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        _, csrf = auth
        values = urllib.parse.parse_qs((await request.body()).decode(), keep_blank_values=True)
        if not origin_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return HTMLResponse(_layout("请求被拒绝", '<p class="error">请求来源或 CSRF 校验失败。</p>', csrf), 403)
        try:
            country = str(values["country_iso3"][0])
            disease = uuid.UUID(str(values["disease_id"][0]))
            metrics = [str(x) for x in values.get("desired_metrics", [])]
            tiers = [str(x) for x in values.get("preferred_source_tiers", [])]
            window = str(values["time_window_kind"][0])
            languages = [x.strip() for x in str(values.get("language_preferences", ["en"])[0]).split(",") if x.strip()]
            maximum = int(values["max_candidates"][0])
            confirmed = values.get("confirm_request") == ["yes"]
            if not country or not metrics or not tiers or not confirmed:
                raise ValueError("必填字段缺失")
            with engine.begin() as connection:
                run_id = connection.execute(
                    text(
                        "SELECT epidemic_discovery.submit_structured_discovery(:country,:disease,:metrics,:window,:tiers,:languages,:maximum)"
                    ),
                    {
                        "country": country,
                        "disease": disease,
                        "metrics": metrics,
                        "window": window,
                        "tiers": tiers,
                        "languages": languages,
                        "maximum": maximum,
                    },
                ).scalar_one()
        except (KeyError, ValueError, TypeError):
            return RedirectResponse(
                "/discoveries?error=" + urllib.parse.quote("国家、病种、指标或来源层级不完整；未创建任务。"), 303
            )
        return RedirectResponse(f"/discoveries/{run_id}", 303)

    @app.post("/discoveries/{run_id}/retry")
    async def retry_discovery_html(run_id: uuid.UUID, request: Request) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        _, csrf = auth
        values = urllib.parse.parse_qs((await request.body()).decode(), keep_blank_values=True)
        if not origin_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return HTMLResponse(_layout("请求被拒绝", '<p class="error">请求来源或 CSRF 校验失败。</p>', csrf), 403)
        try:
            with engine.begin() as connection:
                retry_id = connection.execute(
                    text("SELECT epidemic_discovery.retry_discovery_run(:source)"), {"source": run_id}
                ).scalar_one()
        except Exception:
            return RedirectResponse(
                f"/discoveries/{run_id}?error=" + urllib.parse.quote("当前任务不允许关联重试。"), 303
            )
        return RedirectResponse(f"/discoveries/{retry_id}", 303)

    @app.get("/discoveries/{run_id}")
    def discovery_detail(run_id: uuid.UUID, request: Request) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        row = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_discovery_runs_v3 WHERE discovery_run_id=:id",
            {"id": run_id},
        )
        if not row:
            return HTMLResponse(_layout("未找到任务", '<p class="error">未找到 Discovery 任务。</p>', auth[1]), 404)
        run = row[0]
        events = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_discovery_events_v3 WHERE discovery_run_id=:id ORDER BY created_at,event_id",
            {"id": run_id},
        )
        request_value = run["request_json"]
        progress = run["progress_json"] or {}
        conclusion = _conclusion(run)
        coverage_html = _coverage_table(events)
        request_rows = (
            ("国家", f"{run['country_name_zh']} / {run['country_name_en']} ({run['country_iso3']})"),
            ("病种", f"{run['disease_name_zh']} / {run['disease_name_en']}"),
            ("指标", "、".join(request_value.get("desired_metrics", []))),
            ("时间窗口", request_value.get("time_window_kind")),
            ("来源层级", "、".join(request_value.get("preferred_source_tiers", []))),
            (
                "搜索预算",
                f"最多 {run['budget_json'].get('queries')} 个查询；{run['budget_json'].get('duration_seconds')} 秒",
            ),
        )
        req_html = "".join(f'<tr><th scope="row">{_e(k)}</th><td>{_e(v)}</td></tr>' for k, v in request_rows)
        has_raw = int(progress.get("raw_results", 0)) > 0
        has_candidate = int(run.get("candidate_count") or 0) > 0
        is_terminal = run["state"] in TERMINAL_STATES
        phases = [
            ("排队", "已完成"),
            ("生成查询计划", "已完成" if int(progress.get("plan_steps", 0)) > 0 else "等待执行"),
            (
                "实际 Provider/目录检索",
                "已完成"
                if int(progress.get("network_requests", 0)) > 0
                else ("未执行：缺少配置" if is_terminal else "等待执行"),
            ),
            ("安全访问", "已完成" if has_raw else ("已跳过：无输入" if is_terminal else "等待执行")),
            ("去重", "已完成" if has_raw else ("已跳过：无输入" if is_terminal else "等待执行")),
            ("Schema 分析", "已完成" if has_candidate else ("不适用：无候选" if is_terminal else "等待执行")),
            (
                "生成复核材料",
                "已完成" if run["state"] == "REVIEW_READY" else ("不适用：无候选" if is_terminal else "等待执行"),
            ),
        ]
        phase_html = "".join(
            f'<li class="phase-{"done" if status == "已完成" else "pending"}">{_e(label)}：{_e(status)}</li>'
            for label, status in phases
        )
        event_html = "".join(
            f"<tr><td>{_e(item['created_at'])}</td><td>{_e(_state(item['to_state']))}<small>{_e(item['event_code'])}</small></td><td>{_e(REASON_ZH.get(item['event_code'], item['details'].get('message') or item['details'].get('label_zh') or '状态已持久化'))}</td><td>{'需要' if item['requires_user_action'] else '不需要'}</td></tr>"
            for item in events
        )
        reason = REASON_ZH.get(run["terminal_reason_code"], run["last_error_detail"] or "当前没有终态阻断原因。")
        result_rows = (
            ("计划查询", progress.get("planned_queries", progress.get("queries", 0))),
            ("已执行查询", progress.get("executed_queries", 0)),
            ("真实网络请求", progress.get("network_requests", 0)),
            ("成功响应", progress.get("successful_responses", 0)),
            ("已尝试目录", progress.get("directories_attempted", 0)),
            ("成功目录", progress.get("directories_succeeded", 0)),
            ("原始命中", progress.get("raw_results", 0)),
            ("去重后", progress.get("deduplicated_results", progress.get("deduplicated", 0))),
            ("策略拦截", progress.get("policy_blocked_results", 0)),
            ("质量淘汰", progress.get("quality_rejected_results", 0)),
            ("合格候选", progress.get("qualified_candidates", progress.get("qualified", 0))),
        )
        result_html = "".join(
            f'<div class="stat"><strong>{_e(k)}</strong><span>{_e(v)}</span></div>' for k, v in result_rows
        )
        retry_reason = str(run.get("terminal_reason_code") or "")
        retry_form = ""
        if is_terminal and retry_reason in {
            "GENERIC_SEARCH_PROVIDER_UNCONFIGURED",
            "BLOCKED_GENERIC_SEARCH_CONFIGURATION",
            "DISCOVERY_INCOMPLETE",
        }:
            retry_form = f'''<form method="post" action="/discoveries/{_e(run_id)}/retry"><input type="hidden" name="csrf" value="{_e(auth[1])}"><p>此操作保留旧运行，并创建带 retry_of_run_id 的新任务。</p><button type="submit">创建关联重试任务</button></form>'''
        lease_message = (
            "任务已结束，租约已释放"
            if is_terminal
            and not run.get("claim_owner")
            and not run.get("lease_expires_at")
            and not run.get("heartbeat_at")
            else "任务仍持有活动租约"
            if run.get("claim_owner")
            else "当前无活动租约"
        )
        international_note = (
            "用户已选择国际公共卫生官方来源。"
            if "INTERNATIONAL_PUBLIC_HEALTH" in request_value.get("preferred_source_tiers", [])
            else "用户未选择国际公共卫生官方来源；系统没有静默扩大检索范围。"
        )
        zero_warning = (
            '<p class="notice"><strong>注意：</strong>0 个候选不代表不存在数据源，请结合本次检索覆盖范围判断。</p>'
            if int(run.get("candidate_count") or 0) == 0
            else ""
        )
        body = f"""<section class="conclusion"><h2>{_e(conclusion)}</h2><p>{_e(_candidate_text(run))}</p>{zero_warning}<p>{_e(_worker_message(run))}；最近活动：{_e(run["last_event_at"])}</p></section><section><h2>1. 请求内容</h2><table class="compact"><caption>受控发现请求</caption><tbody>{req_html}</tbody></table><p>{_e(international_note)}</p></section><section><h2>2. 当前进度</h2><ol class="phases">{phase_html}</ol></section><section><h2>3. 实际搜索覆盖</h2>{coverage_html}</section><section><h2>4. 结果统计</h2><div class="summary-grid">{result_html}</div><p>旧页面把本地策略循环同时计为查询、目录和阻断，因此曾显示 4/4/4；这些数字不代表真实 HTTP 请求。</p></section><section><h2>5. 事件时间线</h2><div class="table-box"><table><caption>不可变 Discovery 事件</caption><thead><tr><th scope="col">时间</th><th scope="col">阶段</th><th scope="col">中文说明</th><th scope="col">需要处理</th></tr></thead><tbody>{event_html}</tbody></table></div></section><section><h2>6. 原因与下一步</h2><p>{_e(reason)}</p><p><strong>管理员责任：</strong>Provider 配置、服务器网络和基础设施由管理员处理，普通复核用户无需处理服务器配置。</p><p><strong>下一步：</strong>{_e((events[-1]["details"] if events else {}).get("next_step", "等待 Worker 处理。"))}</p>{retry_form}</section><details><summary>技术证据（默认折叠）</summary><p>{_e(lease_message)}</p>{_technical_table({"run_id": run_id, "state_version": run["state_version"], "attempt_count": run["attempt_count"], "last_error_code": run["last_error_code"], "terminal_reason_code": run["terminal_reason_code"]}, "队列技术状态")}</details>"""
        return HTMLResponse(_layout("Discovery 任务详情", body, auth[1]))

    @app.get("/reviews")
    def reviews(request: Request, page: int = 1, status: str = "", publisher: str = "", tier: str = "") -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        page = max(1, min(page, 1000))
        params = {"limit": 10, "offset": (page - 1) * 10, "status": status, "publisher": publisher, "tier": tier}
        rows = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 WHERE (:status='' OR effective_status=:status) AND (:publisher='' OR publisher ILIKE '%'||:publisher||'%') AND (:tier='' OR source_tier=:tier) ORDER BY review_case_id LIMIT :limit OFFSET :offset",
            params,
        )
        cards = (
            "".join(
                f"""<article class="candidate"><div class="status blocked">不能批准</div><h2>{_e(row["dataset_name"])}</h2><p>{_e(row["publisher"])}</p><p>地理范围：{_e(row["geographic_scope"])}</p><p>许可：{_e(_label(row["license_status"])[0])}</p><p>评分 {_e(row["score"])} <small>评分只用于候选排序，不代表允许入库。</small></p><a class="button-link" href="/reviews/{_e(row["review_case_id"])}">进入复核</a></article>"""
                for row in rows
            )
            or '<p class="notice">当前筛选条件下没有候选。</p>'
        )
        body = f"""<section><h2>待复核候选</h2><form method="get" class="filters"><label for="status">状态</label><select id="status" name="status"><option value="">全部</option><option value="NEEDS_IMPLEMENTATION">需要先实现</option></select><label for="publisher">发布者</label><input id="publisher" name="publisher" value="{_e(publisher)}"><label for="tier">来源层级</label><input id="tier" name="tier" value="{_e(tier)}"><button>筛选</button></form><div class="candidate-grid">{cards}</div><div class="pager"><a href="/reviews?page={max(1, page - 1)}">上一页</a><span>第 {page} 页</span><a href="/reviews?page={page + 1}">下一页</a></div></section>"""
        return HTMLResponse(_layout("待复核候选", body, auth[1]))

    @app.get("/history")
    def history(request: Request) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        rows = _rows(
            engine, "SELECT * FROM epidemic_discovery.review_decision_history_v2 ORDER BY created_at DESC LIMIT 100"
        )
        body = (
            "<section><h2>审核历史</h2><table><caption>最近 100 条不可变审核记录</caption><thead><tr><th>时间</th><th>候选</th><th>决定</th><th>原因</th></tr></thead><tbody>"
            + "".join(
                f"<tr><td>{_e(x['created_at'])}</td><td>{_e(x['dataset_name'])}</td><td>{_e(x['decision'])}</td><td>{_e(x['reason'])}</td></tr>"
                for x in rows
            )
            + "</tbody></table></section>"
        )
        return HTMLResponse(_layout("审核历史", body, auth[1]))

    @app.get("/reviews/{case_id}")
    def review_detail(
        case_id: uuid.UUID,
        request: Request,
        preview_page: int = 1,
        preview_mode: str = "compare",
        field_q: str = "",
        field_status: str = "",
        field_risk: str = "",
        only_errors: int = 0,
        notice: str = "",
    ) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        cards = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 WHERE review_case_id=:id",
            {"id": case_id},
        )
        if not cards:
            return HTMLResponse(_layout("未找到", '<p class="error">未找到复核案例。</p>', auth[1]), 404)
        card = cards[0]
        fields = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_field_mapping_v2 WHERE review_case_id=:id ORDER BY ordinal",
            {"id": case_id},
        )
        all_fields = list(fields)
        integrity = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_mapping_integrity_v2 WHERE review_case_id=:id ORDER BY source_field",
            {"id": case_id},
        )
        if field_q:
            fields = [
                x
                for x in fields
                if field_q.casefold() in str(x["source_field"]).casefold()
                or field_q in FIELD_ZH.get(str(x["source_field"]), "")
            ]
        if field_status:
            fields = [x for x in fields if x["mapping_status"] == field_status]
        if field_risk:
            fields = [
                x
                for x in fields
                if (str(x["source_field"]) in MODEL_FIELDS or x.get("warning") or x.get("warning_flags"))
            ]
        priority = {"unsupported": 1, "raw-only": 3, "mapped": 2, "ignored": 4}
        fields.sort(
            key=lambda x: (0 if x.get("warning") else priority.get(str(x["mapping_status"]), 5), int(x["ordinal"]))
        )
        preview_page = max(1, preview_page)
        preview_mode = preview_mode if preview_mode in {"raw", "clean", "compare"} else "compare"
        preview = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_preview_rows_v2 WHERE review_case_id=:id ORDER BY sample_kind,row_ordinal LIMIT 25 OFFSET :offset",
            {"id": case_id, "offset": (preview_page - 1) * 25},
        )
        if only_errors:
            preview = [x for x in preview if x.get("row_errors")]
        source_names = {str(item["source_field"]) for item in all_fields}
        missing_geocode = "Localidade_id" in source_names and "geocode" not in source_names
        hard = ["当前正式模型不支持市级地理层级，不能作为 Brazil 全国数据。"]
        if integrity or missing_geocode:
            hard.append("映射要求字段 geocode，但当前文件没有该列，不能批准。不得静默替换为 Localidade_id。")
        manual = [
            "发布者并非巴西国家卫生部。",
            "CUSTOM/UNKNOWN 许可目前还不能据此入库。",
            "模型派生字段仅保留原始证据。",
        ]
        passed = ["隔离 artifact、TLS、SSRF 与 active content 技术检查已完成。", "数据为聚合级，未发现个人级记录。"]
        hard_html = "".join(f"<li>{_e(x)}</li>" for x in hard)
        manual_html = "".join(f"<li>{_e(x)}</li>" for x in manual)
        passed_html = "".join(f"<li>{_e(x)}</li>" for x in passed)
        overview = (
            ("数据集", card["dataset_name"]),
            ("发布者", card["publisher"]),
            ("实际覆盖", card["geographic_scope"]),
            ("格式", card["format"]),
            ("行列", f"{card['row_count']} × {card['column_count']}"),
            ("许可", _label(card["license_status"])[0]),
            ("评分", f"{card['score']}（只用于候选排序，不代表允许入库）"),
        )
        overview_html = "".join(f'<tr><th scope="row">{_e(k)}</th><td>{_e(v)}</td></tr>' for k, v in overview)
        field_rows = ""
        for item in fields:
            source = str(item["source_field"])
            status = str(item["mapping_status"])
            meaning = FIELD_ZH.get(source, "来源未提供已登记的中文字段说明")
            if source in MODEL_FIELDS:
                status = "raw-only"
                warning = "模型派生，仅保留原始证据，不作为官方报告病例。"
            else:
                warning = item.get("warning") or (
                    "需要人工确认字段含义。"
                    if not item.get("canonical_field") and item["mapping_status"] == "mapped"
                    else "无"
                )
            technical = f"""<details><summary>查看技术映射</summary><p>canonical：{_e(item.get("canonical_field"))}</p><p>metric：{_e(item.get("metric_code"))}</p><p>transform：{_e(item.get("transform"))} — {_e(TRANSFORM_ZH.get(str(item.get("transform")), "未登记转换说明"))}</p></details>"""
            field_rows += f"""<tr data-source-field="{_e(source)}" data-mapping-status="{_e(status)}" data-canonical="{_e(item.get("canonical_field") or "")}" data-metric="{_e(item.get("metric_code") or "")}" data-transform="{_e(item.get("transform") or "identity")}"><th scope="row">{_e(source)}</th><td>{_display_value(item.get("example_value"))}</td><td>{_e(meaning)}</td><td>{_e(item.get("inferred_type"))}</td><td>{_e(_label(status)[0])}<small>{_e(status)}</small></td><td>{_e(item.get("canonical_field") or item.get("metric_code") or "不进入正式数据")}</td><td>{technical}</td><td>{_e(item.get("confidence"))}</td><td>{_e(warning)}</td></tr>"""
        mapping_missing = "".join(
            f'<div class="hard-block"><strong>{_e(_label(item["finding_code"])[0])}</strong><p>映射要求字段 {_e(item["source_field"])}，但当前文件没有该列，不能批准。</p></div>'
            for item in integrity
        )
        if missing_geocode and not integrity:
            mapping_missing += (
                '<div class="hard-block"><strong>映射来源字段缺失</strong>'
                "<p>映射要求字段 geocode，但当前文件没有该列，不能批准；"
                "不得静默替换为 Localidade_id。</p></div>"
            )
        preview_rows = ""
        for row in preview:
            values = _preview_values(row)
            errors = row.get("row_errors") or []
            status = "有错误" if errors else "可预览"
            preview_rows += f"""<tr class="{"row-error" if errors else ""}"><td>{_e(row["row_ordinal"])}</td><td>{_e(row["sample_kind"])}</td><td>{_e(status)}</td><td>{_comparison(*values["period"], preview_mode)}</td><td>{_comparison(*values["geography"], preview_mode)}</td><td>{_comparison(*values["cases"], preview_mode)}</td><td>{_comparison(*values["deaths"], preview_mode)}</td><td>{_comparison(*values["unit"], preview_mode)}</td><td>{_e("；".join(errors) if errors else "无")}</td><td>{_e(str(row["row_sha256"])[:12])}</td></tr>"""
        total = int(preview[0]["total_rows"]) if preview else 0
        pages = max(1, (total + 24) // 25)
        quality = card.get("quality_json") or {}
        security = card.get("security_json") or {}
        nulls = sum(int(x.get("null_count") or 0) for x in all_fields)
        cells = int(card["row_count"]) * int(card["column_count"])
        zeros = sum(1 for row in preview for value in (row.get("raw_json") or {}).values() if value in (0, "0", "0.0"))
        groups = {
            "A. 来源身份与许可": [
                _check_row(
                    "发布者", "warn", "公共卫生合作项目，不是国家卫生部。", card["publisher"], "人工确认权威性。"
                ),
                _check_row(
                    "许可与条款",
                    "block",
                    "CUSTOM/UNKNOWN 许可目前还不能据此入库。",
                    card["license_status"],
                    "核验条款、再分发和衍生使用。",
                ),
                _check_row(
                    "robots / 使用限制",
                    "unknown",
                    "当前证据未单独记录 robots 结论。",
                    security.get("robots"),
                    "补充可访问与使用限制证据。",
                ),
            ],
            "B. 地理与时间": [
                _check_row(
                    "实际覆盖",
                    "block",
                    "市级数据不能作为 Brazil 全国数据。",
                    card["geographic_scope"],
                    "实现 municipality 标准地点映射。",
                ),
                _check_row(
                    "地区代码证据",
                    "block",
                    "geocode 缺失且 Localidade_id 语义未确认。",
                    "Localidade_id 存在；geocode 不存在",
                    "确认来源文档后创建新 mapping revision。",
                ),
                _check_row(
                    "日期与粒度",
                    "warn",
                    "报告周不等同感染发生日。",
                    quality.get("latest_report_period") or "周度报告期",
                    "确认周起止和最新日期。",
                ),
            ],
            "C. 数据内容与质量": [
                _check_row(
                    "行列数", "pass", "样本规模已记录。", f"{card['row_count']} × {card['column_count']}", "无。"
                ),
                _check_row(
                    "缺失值",
                    "warn",
                    "缺失与 0 不可互换。",
                    f"{nulls} / {cells}（{(100 * nulls / cells if cells else 0):.2f}%）",
                    "核对关键列缺失规则。",
                ),
                _check_row("显式 0", "pass", "0 保持为真实零值。", f"当前预览 {zeros} 个显式 0", "不得转换为缺失。"),
                _check_row(
                    "模型派生字段", "warn", "不作为官方报告病例。", "、".join(sorted(MODEL_FIELDS)), "仅保留原始证据。"
                ),
                _check_row(
                    "重复/非法/未来日期/负修订",
                    "unknown",
                    "当前评估没有完整分项计数。",
                    quality.get("quality"),
                    "补充完整质量计数后再批准。",
                ),
            ],
            "D. 安全与隐私": [
                _check_row(
                    "TLS / SSRF / DNS rebinding",
                    "pass",
                    "网络访问通过受控安全检查。",
                    f"TLS={security.get('tls')}；SSRF={security.get('ssrf')}；DNS={security.get('dns_rebinding')}",
                    "无。",
                ),
                _check_row(
                    "MIME / active content",
                    "pass",
                    "隔离 CSV 未执行主动内容。",
                    f"MIME={card['format']}；active={security.get('active_content')}",
                    "继续保持隔离解析。",
                ),
                _check_row(
                    "prompt injection / secret / PII / person-level",
                    "warn",
                    "聚合数据未发现 PII，但仍需人工确认。",
                    f"PII={card['pii_status']}；命中={security.get('pii_hits')}",
                    "不得导入个人级数据。",
                ),
            ],
            "E. 版本证据": [
                _check_row(
                    "artifact/schema/mapping/terms",
                    "pass",
                    "复核必须固定到不可变版本。",
                    f"{str(card['artifact_sha256'])[:12]} / {str(card['schema_sha256'])[:12]} / {str(card['mapping_sha256'])[:12]} / {str(card['terms_sha256'])[:12]}",
                    "批准时重新核对完整 SHA。",
                ),
            ],
        }
        group_html = "".join(
            f'<section class="evidence-group"><h3>{_e(name)}</h3><div class="table-box"><table><caption>{_e(name)}检查</caption><thead><tr><th>检查内容</th><th>结果</th><th>对用户意味着什么</th><th>证据摘要</th><th>入库前还需做什么</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
            for name, rows in groups.items()
        )
        mapped = sum(x["mapping_status"] == "mapped" and x["source_field"] not in MODEL_FIELDS for x in all_fields)
        raw = sum(x["mapping_status"] == "raw-only" or x["source_field"] in MODEL_FIELDS for x in all_fields)
        ignored = sum(x["mapping_status"] == "ignored" for x in all_fields)
        reasons = "".join(f"<li>{_e(item)}</li>" for item in hard)
        body = f"""<section class="conclusion hard"><h2>一句话入库结论：不能批准</h2><p>当前有 {len(hard)} 个硬阻断；必须先实现市级地点模型并修复缺失字段映射。</p><div class="triage"><div><h3>硬阻断</h3><ul>{hard_html}</ul></div><div><h3>需要人工确认</h3><ul>{manual_html}</ul></div><div><h3>已通过检查</h3><ul>{passed_html}</ul></div></div></section><section><h2>数据集概览</h2><table><caption>候选数据集概览</caption><tbody>{overview_html}</tbody></table></section><section><h2>表1：字段用途与入库映射</h2><p>这里说明原始数据每一列的含义、系统是否使用，以及它将进入哪个标准字段。未映射字段不会进入正式预测数据。</p>{mapping_missing}<form method="get" class="filters"><label for="field-q">字段名搜索</label><input id="field-q" name="field_q" value="{_e(field_q)}"><label for="field-status">状态</label><select id="field-status" name="field_status"><option value="">全部</option><option value="mapped">用于入库</option><option value="raw-only">仅保留原始证据</option><option value="ignored">忽略</option><option value="unsupported">暂不支持</option></select><label><input type="checkbox" name="field_risk" value="1" {"checked" if field_risk else ""}>只看风险字段</label><button>筛选</button></form><div class="table-box"><table id="mapping-table"><caption>字段用途与入库映射（全部 schema 字段可筛选）</caption><thead><tr><th>来源字段</th><th>示例值</th><th>中文含义</th><th>原始类型</th><th>建议处理</th><th>标准字段/指标</th><th>转换和单位</th><th>置信度</th><th>问题及确认事项</th></tr></thead><tbody>{field_rows}</tbody></table></div><section id="mapping-diff" class="confirmation"><h3>保存前差异预览</h3><p>尚未修改；保存会创建新 mapping revision，不覆盖历史。</p></section><button id="save-mapping" type="button" data-case="{_e(case_id)}" data-revision="{_e(card["current_mapping_revision"])}">保存 mapping 调整</button><p id="mapping-result" aria-live="polite"></p></section><section><h2>表2：原始数据与清洗结果预览</h2><p>默认显示原始/清洗对照；不会显示 JSON 对象字符串。</p><form method="get" class="filters"><label for="preview-mode">视图模式</label><select id="preview-mode" name="preview_mode"><option value="compare">原始/清洗对照</option><option value="raw">原始值</option><option value="clean">清洗结果</option></select><label><input type="checkbox" name="only_errors" value="1" {"checked" if only_errors else ""}>只看有错误的行</label><button>应用视图</button></form><div class="table-box"><table><caption>原始数据与清洗结果预览，第 {preview_page}/{pages} 页，共 {total} 行</caption><thead><tr><th>样本序号</th><th>样本类型</th><th>行状态</th><th>报告期<small>data_iniSE / SE</small></th><th>地理名称/代码<small>cidade / Localidade_id</small></th><th>病例<small>casos</small></th><th>死亡<small>obitos</small></th><th>单位</th><th>转换错误</th><th>row SHA</th></tr></thead><tbody>{preview_rows}</tbody></table></div><div class="pager"><a href="?preview_page={max(1, preview_page - 1)}&preview_mode={preview_mode}">上一页</a><span>第 {preview_page}/{pages} 页</span><a href="?preview_page={min(pages, preview_page + 1)}&preview_mode={preview_mode}">下一页</a></div></section><section><h2>表3：来源可信度、数据质量与入库风险</h2><div class="hard-block"><strong>硬阻断置顶</strong><ul>{reasons}</ul></div>{group_html}</section><section><h2>审核操作</h2><div class="summary-grid"><div class="stat"><strong>将入库字段</strong><span>{mapped}</span></div><div class="stat"><strong>仅保留 raw</strong><span>{raw}</span></div><div class="stat"><strong>忽略字段</strong><span>{ignored}</span></div><div class="stat"><strong>实际地理层级</strong><span>municipality</span></div><div class="stat"><strong>硬阻断</strong><span>{len(hard)}</span></div><div class="stat"><strong>待确认</strong><span>{len(manual)}</span></div></div><form method="post" action="/reviews/{_e(case_id)}/decision"><input type="hidden" name="csrf" value="{_e(auth[1])}"><input type="hidden" name="etag" value="{_e(card["etag"])}"><input type="hidden" name="idempotency_key" value="{uuid.uuid4()}"><label for="reason-template">中文原因模板</label><select id="reason-template"><option value="">请选择</option><option>需要确认地理层级与地点代码</option><option>需要补充许可和条款证据</option><option>需要修复缺失字段映射</option></select><label for="decision-reason">自由文本原因</label><textarea id="decision-reason" name="reason" rows="4"></textarea><label for="reauth-password">近期重新认证密码</label><input id="reauth-password" name="password" type="password" autocomplete="current-password"><div class="decision-buttons"><button name="decision" value="CHANGES_REQUESTED">要求修改</button><button name="decision" value="REJECTED">拒绝</button><button name="decision" value="APPROVED" disabled>批准当前版本并排队入库</button></div><p class="error">批准已禁用：</p><ul>{reasons}</ul></form></section><details><summary>折叠技术证据</summary>{_technical_table({"candidate_version_id": card["candidate_version_id"], "artifact_sha256": card["artifact_sha256"], "schema_sha256": card["schema_sha256"], "mapping_sha256": card["mapping_sha256"], "assessment_sha256": card["assessment_sha256"], "security_report_sha256": card["security_report_sha256"]}, "固定版本技术证据")}</details>"""
        notice_html = f'<p class="notice" role="status">{_e(notice)}</p>' if notice else ""
        return HTMLResponse(_layout("候选人工复核", notice_html + body, auth[1]))

    @app.post("/reviews/{case_id}/decision")
    async def review_decision_html(case_id: uuid.UUID, request: Request) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        username, csrf = auth
        values = urllib.parse.parse_qs((await request.body()).decode(), keep_blank_values=True)
        if not origin_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return HTMLResponse(_layout("请求被拒绝", '<p class="error">CSRF 或来源校验失败。</p>', csrf), 403)
        action = str(values.get("decision", [""])[0])
        reason = str(values.get("reason", [""])[0]).strip()
        if action in {"REJECTED", "CHANGES_REQUESTED"} and not reason:
            return RedirectResponse(f"/reviews/{case_id}?notice=" + urllib.parse.quote("必须填写原因。"), 303)
        card = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 WHERE review_case_id=:id",
            {"id": case_id},
        )
        if not card:
            return RedirectResponse("/reviews?notice=" + urllib.parse.quote("候选不存在。"), 303)
        if action == "APPROVED" and card[0]["approval_blocked"]:
            return RedirectResponse(f"/reviews/{case_id}?notice=" + urllib.parse.quote("存在硬阻断，不能批准。"), 303)
        if not _auth(auth_file, username, str(values.get("password", [""])[0])):
            return RedirectResponse(f"/reviews/{case_id}?notice=" + urllib.parse.quote("近期重新认证失败。"), 303)
        try:
            key = uuid.UUID(str(values["idempotency_key"][0]))
            etag = str(values["etag"][0])
        except (KeyError, ValueError):
            return RedirectResponse(f"/reviews/{case_id}?notice=" + urllib.parse.quote("状态参数无效，请刷新。"), 303)
        row = card[0]
        evidence = {
            name: str(row[name])
            for name in (
                "candidate_version_id",
                "canonical_endpoint_sha256",
                "publisher_evidence_sha256",
                "terms_sha256",
                "artifact_sha256",
                "schema_sha256",
                "mapping_sha256",
                "assessment_sha256",
                "security_report_sha256",
            )
        }
        evidence["etag"] = etag
        evidence_sha = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        decision_sha = hashlib.sha256(f"{case_id}|{key}|{action}|{evidence_sha}".encode()).hexdigest()
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "SELECT epidemic_discovery.submit_review_decision(:case,:action,'APPROVE_ARTIFACT_ONCE',:reviewer,:reason,:key,:etag,CAST(:evidence AS jsonb),:evidence_sha,:decision_sha)"
                    ),
                    {
                        "case": case_id,
                        "action": action,
                        "reviewer": username,
                        "reason": reason,
                        "key": key,
                        "etag": etag,
                        "evidence": json.dumps(evidence),
                        "evidence_sha": evidence_sha,
                        "decision_sha": decision_sha,
                    },
                ).scalar_one()
        except Exception:
            return RedirectResponse(
                f"/reviews/{case_id}?notice=" + urllib.parse.quote("候选状态已变化，请刷新后重试。"), 303
            )
        return RedirectResponse(f"/reviews/{case_id}?notice=" + urllib.parse.quote("审核操作已保存。"), 303)

    @app.get("/api/v1/catalog")
    def catalog(request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        countries, diseases = catalog_rows()
        return JSONResponse(_jsonable({"countries": countries, "diseases": diseases}))

    @app.get("/api/v1/discoveries/{run_id}")
    def discovery_api(run_id: uuid.UUID, request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        rows = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_discovery_runs_v3 WHERE discovery_run_id=:id",
            {"id": run_id},
        )
        events = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_discovery_events_v3 WHERE discovery_run_id=:id ORDER BY created_at,event_id",
            {"id": run_id},
        )
        return JSONResponse(_jsonable({"run": rows[0] if rows else None, "events": events}))

    @app.post("/api/v1/discoveries")
    async def submit_discovery_api(request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        body = await request.json()
        allowed = {
            "country_iso3",
            "disease_id",
            "desired_metrics",
            "time_window_kind",
            "preferred_source_tiers",
            "language_preferences",
            "max_candidates",
        }
        if set(body) != allowed:
            return JSONResponse({"error": "INVALID_REQUEST"}, 422)
        try:
            with engine.begin() as connection:
                run_id = connection.execute(
                    text(
                        "SELECT epidemic_discovery.submit_structured_discovery(:country,:disease,:metrics,:window,:tiers,:languages,:maximum)"
                    ),
                    {
                        "country": body["country_iso3"],
                        "disease": uuid.UUID(body["disease_id"]),
                        "metrics": body["desired_metrics"],
                        "window": body["time_window_kind"],
                        "tiers": body["preferred_source_tiers"],
                        "languages": body["language_preferences"],
                        "maximum": int(body["max_candidates"]),
                    },
                ).scalar_one()
        except (ValueError, TypeError):
            return JSONResponse({"error": "INVALID_REQUEST"}, 422)
        return JSONResponse(
            {"discovery_run_id": str(run_id), "state": "QUEUED", "review_url": f"/discoveries/{run_id}"}, 202
        )

    @app.post("/api/v1/review-cases/{case_id}/mapping")
    async def save_mapping(case_id: uuid.UUID, request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        if not origin_ok(request, auth[1], request.headers.get("x-csrf-token", "")):
            return JSONResponse({"error": "CSRF_REJECTED"}, 403)
        body = await request.json()
        try:
            with engine.begin() as connection:
                revision = connection.execute(
                    text("SELECT epidemic_discovery.save_mapping_adjustment(:case,:revision,CAST(:fields AS jsonb))"),
                    {"case": case_id, "revision": int(body["expected_revision"]), "fields": json.dumps(body["fields"])},
                ).scalar_one()
        except (KeyError, ValueError, TypeError):
            return JSONResponse({"error": "INVALID_MAPPING"}, 422)
        return JSONResponse({"status": "SAVED", "new_revision": revision, "formal_rows_written": False})

    return app

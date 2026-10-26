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

COOKIE = "epidemic_source_review"
IDLE_SECONDS = 1_800
ABSOLUTE_SECONDS = 28_800
ORIGIN = "http://127.0.0.1:8021"
STATIC_VERSION = "p1-7ar-v1"


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


def _layout(title: str, body: str, csrf: str = "") -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="{_e(csrf)}"><title>{_e(title)}</title><link rel="stylesheet" href="/static/review.p1-7ar-v1.css"></head><body><header><div><h1>疫情数据源复核台</h1><p>隔离预览，不属于正式疫情数据库。</p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><nav aria-label="主导航"><a href="/discoveries">发现新数据源</a><a href="/reviews">待复核候选</a><a href="/history">审核历史</a></nav><main>{body}</main><script src="/static/review.p1-7ar-v1.js" defer></script></body></html>"""


def _login(error: str = "") -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>数据源复核登录</title><link rel="stylesheet" href="/static/review.p1-7ar-v1.css"></head><body class="login-page"><main class="login-card"><h1>数据源复核登录</h1><p>请输入独立 reviewer 凭据。</p>{error}<form method="post" action="/login"><label for="username">用户名</label><input id="username" name="username" autocomplete="username" required><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required><button>登录</button></form></main></body></html>"""


def _candidate_cards(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<p class="notice">当前筛选条件下没有候选。</p>'
    cards = []
    for row in rows:
        risks = row.get("risk_flags") or []
        cards.append(
            f"""<article class="candidate"><div class="status blocked">{_e(row["effective_status"])}（需要实现）</div><h2>{_e(row["dataset_name"])}</h2><dl><dt>发布者</dt><dd>{_e(row["publisher"])}</dd><dt>来源层级</dt><dd>{_e(row["source_tier"])}</dd><dt>地理范围</dt><dd>{_e(row["geographic_scope"])}</dd><dt>格式 / 行列</dt><dd>{_e(row["format"])} / {_e(row["row_count"])}×{_e(row["column_count"])}</dd><dt>许可</dt><dd>{_e(row["license_status"])}</dd><dt>风险</dt><dd>{len(risks)} 项</dd><dt>评分</dt><dd>{_e(row["score"])}</dd></dl><a class="button-link" href="/reviews/{_e(row["review_case_id"])}">进入复核</a></article>"""
        )
    return "".join(cards)


def create_app() -> FastAPI:
    engine = create_engine(
        os.environ["REVIEW_DATABASE_URL"],
        connect_args={"options": "-c default_transaction_read_only=off -c statement_timeout=5000"},
        hide_parameters=True,
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
        value = current(request)
        return value if value else RedirectResponse("/login", 303)

    def api_auth(request: Request) -> tuple[str, str] | JSONResponse:
        value = current(request)
        return value if value else JSONResponse({"error": "UNAUTHORIZED"}, 401)

    def write_guard(request: Request, csrf: str) -> JSONResponse | None:
        referer = request.headers.get("referer", "")
        if (
            request.headers.get("origin") != ORIGIN
            or not referer.startswith(ORIGIN + "/")
            or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf)
        ):
            return JSONResponse({"error": "CSRF_REJECTED", "message": "请求来源或 CSRF 校验失败。"}, 403)
        return None

    @app.get("/healthz")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/static/review.p1-7ar-v1.css")
    def css() -> FileResponse:
        return FileResponse(static_dir / "review.p1-7ar-v1.css", media_type="text/css")

    @app.get("/static/review.p1-7ar-v1.js")
    def js() -> FileResponse:
        return FileResponse(static_dir / "review.p1-7ar-v1.js", media_type="application/javascript")

    @app.get("/login")
    def login_page() -> HTMLResponse:
        return HTMLResponse(_login())

    @app.post("/login")
    async def login(request: Request) -> Response:
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        username = str(values.get("username", [""])[0])
        password = str(values.get("password", [""])[0])
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

    @app.get("/discoveries")
    def discoveries(request: Request) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        _, csrf = auth
        runs = _rows(
            engine, "SELECT * FROM epidemic_discovery.review_discovery_runs_v2 ORDER BY created_at DESC LIMIT 50"
        )
        run_rows = "".join(
            f"<tr><td>{_e(x['created_at'])}</td><td>{_e(x['state'])}</td><td>{_e(x['request_json'].get('country_iso3'))}</td><td>{_e(x['request_json'].get('time_window_kind'))}</td><td>{_e(x['candidate_count'])}</td></tr>"
            for x in runs
        )
        body = f"""<section><h2>发现新数据源</h2><p>只接受结构化 catalog 选择；不接受 URL、SQL、shell、header、Cookie 或 adapter 代码。</p><form id="discovery-form"><label for="discover-country">国家（249 国 catalog）</label><input id="discover-country" role="combobox" list="discover-country-list" aria-controls="discover-country-list" required><datalist id="discover-country-list"></datalist><label for="discover-disease">病种</label><input id="discover-disease" role="combobox" list="discover-disease-list" aria-controls="discover-disease-list" required><datalist id="discover-disease-list"></datalist><fieldset><legend>所需指标</legend><label><input type="checkbox" name="metric" value="cases_new" checked>新增病例</label><label><input type="checkbox" name="metric" value="deaths_new">新增死亡</label><label><input type="checkbox" name="metric" value="incidence_rate">发病率</label></fieldset><label for="discover-window">时间窗口</label><select id="discover-window"><option value="latest_rolling_year">最近滚动一年</option><option value="latest_complete_calendar_year">最近完整日历年</option><option value="current_year_to_latest_period">本年至最新报告期</option></select><fieldset><legend>来源层级</legend><label><input type="checkbox" name="tier" value="NATIONAL_HEALTH" checked>国家卫生机构</label><label><input type="checkbox" name="tier" value="GOVERNMENT_OPEN_DATA" checked>政府开放数据</label><label><input type="checkbox" name="tier" value="REGIONAL_HEALTH">区域卫生机构</label><label><input type="checkbox" name="tier" value="PUBLIC_HEALTH_RESEARCH">公共卫生研究合作</label></fieldset><label for="discover-languages">语言偏好（逗号分隔）</label><input id="discover-languages" value="zh,pt,en"><label for="discover-max">最大候选数（1～20）</label><input id="discover-max" type="number" min="1" max="20" value="5"><button type="submit">开始受控检索</button><p id="discovery-result" aria-live="polite"></p></form></section><section><h2>最近发现任务</h2><div class="table-box"><table><caption>最近 50 个 Discovery Run</caption><thead><tr><th scope="col">创建时间</th><th scope="col">状态</th><th scope="col">国家</th><th scope="col">时间窗口</th><th scope="col">候选数</th></tr></thead><tbody>{run_rows}</tbody></table></div></section>"""
        return HTMLResponse(_layout("发现新数据源", body, csrf))

    @app.get("/reviews")
    def reviews(request: Request, page: int = 1, status: str = "", publisher: str = "", tier: str = "") -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        _, csrf = auth
        page = max(1, min(page, 1000))
        params = {"limit": 10, "offset": (page - 1) * 10, "status": status, "publisher": publisher, "tier": tier}
        rows = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 WHERE (:status='' OR effective_status=:status) AND (:publisher='' OR publisher ILIKE '%'||:publisher||'%') AND (:tier='' OR source_tier=:tier) ORDER BY review_case_id LIMIT :limit OFFSET :offset",
            params,
        )
        body = f"""<section><h2>待复核候选</h2><form method="get" class="filters"><label for="status">状态</label><select id="status" name="status"><option value="">全部</option><option value="NEEDS_IMPLEMENTATION">需要实现</option></select><label for="publisher">发布者</label><input id="publisher" name="publisher" value="{_e(publisher)}"><label for="tier">来源层级</label><input id="tier" name="tier" value="{_e(tier)}"><button>筛选</button></form><div class="candidate-grid">{_candidate_cards(rows)}</div><div class="pager"><a href="/reviews?page={max(1, page - 1)}">上一页</a><span>第 {page} 页</span><a href="/reviews?page={page + 1}">下一页</a></div></section>"""
        return HTMLResponse(_layout("待复核候选", body, csrf))

    @app.get("/history")
    def history(request: Request) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        rows = _rows(
            engine, "SELECT * FROM epidemic_discovery.review_decision_history_v2 ORDER BY created_at DESC LIMIT 100"
        )
        body = (
            '<h2>审核历史</h2><div class="table-box"><table><caption>最近 100 条不可变审核记录</caption><thead><tr><th scope="col">时间</th><th scope="col">候选</th><th scope="col">决定</th><th scope="col">原因</th></tr></thead><tbody>'
            + "".join(
                f"<tr><td>{_e(x['created_at'])}</td><td>{_e(x['dataset_name'])}</td><td>{_e(x['decision'])}</td><td>{_e(x['reason'])}</td></tr>"
                for x in rows
            )
            + "</tbody></table></div>"
        )
        return HTMLResponse(_layout("审核历史", body, auth[1]))

    @app.get("/reviews/{case_id}")
    def review_detail(case_id: uuid.UUID, request: Request, preview_page: int = 1) -> Response:
        auth = page_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
        _, csrf = auth
        cards = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 WHERE review_case_id=:id",
            {"id": case_id},
        )
        if not cards:
            return HTMLResponse(_layout("未找到", '<p class="error">未找到复核案例。</p>', csrf), 404)
        card = cards[0]
        fields = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_field_mapping_v2 WHERE review_case_id=:id ORDER BY ordinal",
            {"id": case_id},
        )
        integrity = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_mapping_integrity_v2 WHERE review_case_id=:id ORDER BY source_field",
            {"id": case_id},
        )
        preview_page = max(1, preview_page)
        preview = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_preview_v WHERE review_case_id=:id ORDER BY sample_kind,row_ordinal LIMIT 25 OFFSET :offset",
            {"id": case_id, "offset": (preview_page - 1) * 25},
        )
        risks = [
            "仅覆盖里约热内卢市，不代表巴西全国",
            "发布方是 Fiocruz 关联公共卫生合作项目，不是巴西国家卫生部",
            "包含模型派生字段",
            "非标准许可仍需人工核验",
            "模型派生字段不得映射为官方报告病例",
            "市级数据不得以 Brazil 全国粒度入库",
        ]
        if integrity:
            risks.append(
                "geocode 不在真实 31 列 schema 中：MAPPING_SOURCE_FIELD_MISSING；不得静默替换为 Localidade_id。"
            )
        risk_html = "".join(f"<li>{_e(x)}</li>" for x in risks)
        field_rows = "".join(
            f'<tr data-source-field="{_e(x["source_field"])}" data-mapping-status="{_e(x["mapping_status"])}" data-canonical="{_e(x["canonical_field"] if x["canonical_field"] is not None else "")}" data-metric="{_e(x["metric_code"] if x["metric_code"] is not None else "")}" data-transform="{_e(x["transform"] if x["transform"] is not None else "identity")}" data-source-unit="{_e(x["source_unit"] if x["source_unit"] is not None else "")}" data-target-unit="{_e(x["target_unit"] if x["target_unit"] is not None else "")}" data-confidence="{_e(x["confidence"] if x["confidence"] is not None else "0.5")}" data-warning="{_e(x["warning"] if x["warning"] is not None else "")}"><th scope="row">{_e(x["source_field"])}</th><td>{_e(x["example_value"])}</td><td>{_e(x["inferred_type"])}</td><td class="mapping-status">{_e(x["mapping_status"])}</td><td class="mapping-canonical">{_e(x["canonical_field"])}</td><td class="mapping-metric">{_e(x["metric_code"])}</td><td class="mapping-transform">{_e(x["transform"])}</td><td>{_e(x["source_unit"])} → {_e(x["target_unit"])}</td><td>{_e(x["confidence"])}</td><td>{_e(x["warning"])}</td></tr>'
            for x in fields
        )
        preview_rows = "".join(
            "<tr>"
            + "".join(
                f"<td>{_e(value)}</td>"
                for value in [
                    x["sample_kind"],
                    x["row_ordinal"],
                    json.dumps(x["sanitized_json"], ensure_ascii=False),
                    "；".join(x["row_errors"] or []) or "无",
                    str(x["row_sha256"])[:12],
                ]
            )
            + "</tr>"
            for x in preview
        )
        evidence = {
            key: card.get(key)
            for key in (
                "publisher",
                "source_tier",
                "geographic_scope",
                "license_status",
                "format",
                "row_count",
                "column_count",
                "quality_json",
                "security_json",
                "pii_status",
                "score",
                "risk_flags",
                "artifact_sha256",
                "schema_sha256",
                "mapping_sha256",
            )
        }
        quality = card.get("quality_json") or {}
        security = card.get("security_json") or {}
        quality_detail = quality.get("quality") or {}
        evidence_rows = (
            ("发布者身份与官方性", f"{card['publisher']}；{quality.get('officiality', '非国家卫生部来源')}"),
            ("地理覆盖", card["geographic_scope"]),
            ("许可状态", card["license_status"]),
            ("robots", security.get("robots", "当前证据未单独记录")),
            ("TLS", security.get("tls", "未记录")),
            ("SSRF", security.get("ssrf", "未记录")),
            ("DNS rebinding", security.get("dns_rebinding", "未记录")),
            ("PII", f"{card['pii_status']}；命中 {security.get('pii_hits', '未记录')}"),
            ("active content", security.get("active_content", "未记录")),
            ("行列数", f"{card['row_count']} × {card['column_count']}"),
            ("缺失值单元格", sum(int(item.get("null_count") or 0) for item in fields)),
            ("重复记录", quality_detail.get("duplicates", "当前评估未单独记录")),
            ("非法/未来日期", quality_detail.get("invalid_or_future_dates", "当前评估未单独记录")),
            ("模型派生字段", "存在；不得映射为官方报告病例"),
            ("综合评分与风险", f"{card['score']}；{len(card.get('risk_flags') or [])} 项风险"),
            (
                "artifact/schema/mapping SHA",
                f"{str(card['artifact_sha256'])[:12]} / {str(card['schema_sha256'])[:12]} / {str(card['mapping_sha256'])[:12]}",
            ),
        )
        evidence_table_rows = "".join(
            f'<tr><th scope="row">{_e(label)}</th><td>{_e(value)}</td></tr>' for label, value in evidence_rows
        )
        confirmations = "".join(
            f'<label><input type="checkbox" name="approval-confirmation" value="{key}">{label}</label>'
            for key, label in (
                ("publisher", "发布者身份"),
                ("license", "许可状态"),
                ("aggregate", "聚合数据且无 PII"),
                ("geography", "国家及地理层级"),
                ("disease", "病种"),
                ("dates", "日期语义和粒度"),
                ("measure", "count/rate/percentage"),
                ("unit", "单位"),
                ("mapping", "mapping"),
                ("versions", "固定 artifact/mapping 版本"),
            )
        )
        body = f"""<section class="risk-panel"><h2>{_e(card["dataset_name"])}</h2><div class="status blocked">NEEDS_IMPLEMENTATION / UNSUPPORTED_GEOGRAPHIC_GRAIN</div><h3>批准阻断原因</h3><ul>{risk_html}</ul></section><section><h2>表1：来源与字段映射（全部 {_e(card["column_count"])} 列）</h2><p>保存调整只会追加新 proposal revision；不会覆盖历史或入库。所有编辑控件均为白名单下拉框。</p><div class="table-box"><table id="mapping-table"><caption>来源字段、状态与声明式映射</caption><thead><tr><th scope="col">来源字段</th><th scope="col">示例值</th><th scope="col">类型</th><th scope="col">状态</th><th scope="col">canonical</th><th scope="col">metric</th><th scope="col">transform</th><th scope="col">单位</th><th scope="col">置信度</th><th scope="col">warning</th></tr></thead><tbody>{field_rows}</tbody></table></div><button id="save-mapping" type="button" data-case="{_e(case_id)}" data-revision="{_e(card["current_mapping_revision"])}">保存 mapping 调整建议</button><p id="mapping-result" aria-live="polite"></p></section><section><h2>表2：数据预览</h2><p>每页 25 行；null 显示“缺失”，显式 0 保持为“0”。</p><div class="table-box"><table><caption>隔离数据预览，第 {preview_page} 页</caption><thead><tr><th scope="col">样本</th><th scope="col">行号</th><th scope="col">清洗预览</th><th scope="col">行错误</th><th scope="col">row SHA</th></tr></thead><tbody>{preview_rows}</tbody></table></div><div class="pager"><a href="/reviews/{_e(case_id)}?preview_page={max(1, preview_page - 1)}">上一页</a><span>第 {preview_page} 页</span><a href="/reviews/{_e(case_id)}?preview_page={preview_page + 1}">下一页</a></div></section><section><h2>表3：数据质量与来源证据</h2><div class="table-box"><table><caption>来源、安全、质量与固定版本证据</caption><thead><tr><th scope="col">检查项</th><th scope="col">结果</th></tr></thead><tbody>{evidence_table_rows}</tbody></table></div><details><summary>技术证据（默认折叠）</summary><pre>{_e(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))}</pre></details></section><section><h2>审核操作</h2><fieldset class="approval-confirmations"><legend>批准前逐项确认</legend>{confirmations}</fieldset><label for="decision-reason">原因（要求修改或拒绝时必填）</label><textarea id="decision-reason" rows="4"></textarea><label for="reauth-password">近期重新认证密码</label><input id="reauth-password" type="password" autocomplete="current-password"><div class="decision-buttons"><button type="button" data-decision="CHANGES_REQUESTED" data-case="{_e(case_id)}" data-etag="{_e(card["etag"])}">要求修改</button><button type="button" data-decision="REJECTED" data-case="{_e(case_id)}" data-etag="{_e(card["etag"])}">拒绝候选</button><button type="button" data-decision="APPROVED" data-case="{_e(case_id)}" data-etag="{_e(card["etag"])}" disabled title="市级地理粒度和缺失 mapping 字段尚未实现">批准当前版本并排队入库（已禁用）</button></div><p id="decision-result" aria-live="polite"></p></section>"""
        return HTMLResponse(_layout("候选详情", body, csrf))

    @app.get("/api/v1/catalog")
    def catalog(request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        rows = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_discovery_catalog_v2 ORDER BY country_name_en,disease_name_en",
        )
        countries, diseases = {}, {}
        for row in rows:
            countries[row["country_iso3"]] = {
                k: row[k] for k in ("country_iso2", "country_iso3", "country_name_en", "country_name_zh")
            }
            diseases[str(row["disease_id"])] = {
                k: row[k] for k in ("disease_id", "disease_code", "disease_name_en", "disease_name_zh")
            }
        payload = {"countries": list(countries.values()), "diseases": list(diseases.values())}
        return JSONResponse(json.loads(json.dumps(payload, default=str)))

    @app.post("/api/v1/discoveries")
    async def submit_discovery(request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        blocked = write_guard(request, auth[1])
        if blocked:
            return blocked
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
        return JSONResponse({"discovery_run_id": str(run_id), "state": "QUEUED", "review_required": True}, 202)

    @app.get("/api/v1/review-cases")
    def cases(request: Request, page: int = 1, page_size: int = 10) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        if page < 1 or not 1 <= page_size <= 50:
            return JSONResponse({"error": "INVALID_PAGINATION"}, 400)
        items = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 ORDER BY review_case_id LIMIT :limit OFFSET :offset",
            {"limit": page_size, "offset": (page - 1) * page_size},
        )
        payload = {
            "review_required": True,
            "formal_imported": False,
            "page": page,
            "page_size": page_size,
            "items": items,
        }
        return JSONResponse(json.loads(json.dumps(payload, default=str)))

    @app.get("/api/v1/review-cases/{case_id}")
    def detail(case_id: uuid.UUID, request: Request, preview_page: int = 1) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        card = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 WHERE review_case_id=:id",
            {"id": case_id},
        )
        mapping = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_field_mapping_v2 WHERE review_case_id=:id ORDER BY ordinal",
            {"id": case_id},
        )
        integrity = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_mapping_integrity_v2 WHERE review_case_id=:id ORDER BY source_field",
            {"id": case_id},
        )
        preview = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_preview_v WHERE review_case_id=:id ORDER BY sample_kind,row_ordinal LIMIT 25 OFFSET :offset",
            {"id": case_id, "offset": (max(1, preview_page) - 1) * 25},
        )
        return JSONResponse(
            json.loads(
                json.dumps(
                    {
                        "candidate": card[0] if card else None,
                        "mapping_table": mapping,
                        "preview_table": preview,
                        "integrity": integrity,
                        "raw_path_exposed": False,
                    },
                    default=str,
                )
            )
        )

    @app.post("/api/v1/review-cases/{case_id}/mapping")
    async def save_mapping(case_id: uuid.UUID, request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        blocked = write_guard(request, auth[1])
        if blocked:
            return blocked
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

    @app.post("/api/v1/review-cases/{case_id}/decision")
    async def decision(case_id: uuid.UUID, request: Request) -> Response:
        auth = api_auth(request)
        if isinstance(auth, JSONResponse):
            return auth
        username, csrf = auth
        blocked = write_guard(request, csrf)
        if blocked:
            return blocked
        body = await request.json()
        action = str(body.get("decision"))
        reason = str(body.get("reason", "")).strip()
        if action in {"REJECTED", "CHANGES_REQUESTED"} and not reason:
            return JSONResponse({"error": "REASON_REQUIRED"}, 422)
        card = _rows(
            engine,
            "SELECT * FROM epidemic_discovery.review_candidate_cards_v2 WHERE review_case_id=:id",
            {"id": case_id},
        )
        if not card:
            return JSONResponse({"error": "NOT_FOUND"}, 404)
        confirmations = body.get("confirmations", [])
        required = {
            "publisher",
            "license",
            "aggregate",
            "geography",
            "disease",
            "dates",
            "measure",
            "unit",
            "mapping",
            "versions",
        }
        if action == "APPROVED" and set(confirmations) != required:
            return JSONResponse(
                {"error": "APPROVAL_CONFIRMATIONS_REQUIRED", "message": "批准前必须逐项完成全部确认。"}, 422
            )
        if action == "APPROVED" and card[0]["approval_blocked"]:
            return JSONResponse(
                {
                    "error": "UNSUPPORTED_GEOGRAPHIC_GRAIN",
                    "message": "市级地理粒度和缺失 mapping 字段尚未实现，不能批准。",
                },
                409,
            )
        if not _auth(auth_file, username, str(body.get("password", ""))):
            return JSONResponse({"error": "RECENT_REAUTH_REQUIRED"}, 401)
        try:
            key = uuid.UUID(request.headers.get("idempotency-key", ""))
        except ValueError:
            return JSONResponse({"error": "IDEMPOTENCY_KEY_REQUIRED"}, 422)
        etag = request.headers.get("if-match", "")
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
        evidence.update({"etag": etag, "scope": str(body.get("scope", "APPROVE_ARTIFACT_ONCE"))})
        evidence_sha = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        decision_sha = hashlib.sha256(f"{case_id}|{key}|{action}|{evidence_sha}".encode()).hexdigest()
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "SELECT epidemic_discovery.submit_review_decision(:case,:action,:scope,:reviewer,:reason,:key,:etag,CAST(:evidence AS jsonb),:evidence_sha,:decision_sha)"
                    ),
                    {
                        "case": case_id,
                        "action": action,
                        "scope": str(body.get("scope", "APPROVE_ARTIFACT_ONCE")),
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
            return JSONResponse({"error": "APPROVAL_STALE", "message": "候选状态已变化，请刷新后重试。"}, 409)
        return JSONResponse({"state": action, "promotion_queued": False, "formal_rows_written": False})

    return app

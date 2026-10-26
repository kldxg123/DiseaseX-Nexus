from __future__ import annotations

# ruff: noqa: E501
# mypy: ignore-errors
import hashlib
import hmac
import html
import json
import os
import secrets
import time
import urllib.parse
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine, create_engine, text

COOKIE = "epidemic_p1_9_dashboard"
SESSION_SECONDS = 8 * 60 * 60


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


class Auth:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.username = str(raw["username"])
        self.salt = bytes.fromhex(str(raw["salt_hex"]))
        self.expected = bytes.fromhex(str(raw["hash_hex"]))
        self.n, self.r, self.p = int(raw["n"]), int(raw["r"]), int(raw["p"])

    def verify(self, username: str, password: str) -> bool:
        actual = hashlib.scrypt(password.encode(), salt=self.salt, n=self.n, r=self.r, p=self.p, dklen=32)
        return hmac.compare_digest(username, self.username) and hmac.compare_digest(actual, self.expected)


class Sessions:
    def __init__(self) -> None:
        self.tokens: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self.tokens[token] = time.time() + SESSION_SECONDS
        return token

    def valid(self, token: str | None) -> bool:
        return bool(token and self.tokens.get(token, 0) > time.time())


def _layout(title: str, body: str) -> HTMLResponse:
    style = """
    :root{--ink:#101828;--blue:#175cd3;--red:#b42318;--line:#d0d5dd}*{box-sizing:border-box}
    body{margin:0;font-family:system-ui,sans-serif;background:#f2f4f7;color:var(--ink)}.banner{background:var(--red);
    color:white;padding:14px;text-align:center;font-weight:900}header,main{max-width:1280px;margin:auto;padding:18px}
    header{display:flex;justify-content:space-between;align-items:center}.card{background:white;border:1px solid var(--line);
    border-radius:12px;padding:18px;margin-bottom:16px}.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
    label{display:flex;flex-direction:column;gap:5px}input,select,button{padding:9px;border:1px solid #98a2b3;border-radius:6px}
    button,.button{background:var(--blue);color:#fff;border:0;text-decoration:none;display:inline-block;padding:9px 13px;border-radius:6px}
    .summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.stat{background:#f9fafb;
    border-radius:8px;padding:12px}.warning{background:#fffaeb;border-left:5px solid #f79009;padding:12px}.panels{display:grid;
    grid-template-columns:repeat(3,1fr);gap:14px}.bar{height:14px;background:#d1e9ff;margin:3px 0;position:relative}.bar span{
    display:block;height:100%;background:#1570ef}.bar-label{display:flex;justify-content:space-between;font-size:.85rem}
    table{width:100%;border-collapse:collapse}th,td{border:1px solid var(--line);padding:7px;text-align:left}.scroll{overflow:auto}
    .pill{background:#eef4ff;border-radius:12px;padding:3px 7px}.danger{color:var(--red);font-weight:700}
    @media(max-width:850px){.panels{grid-template-columns:1fr}header{display:block}.filters{grid-template-columns:1fr}.hide-mobile{display:none}}
    """
    return HTMLResponse(
        f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title><style>{style}</style></head><body><div class='banner'>P1-9临时演示环境——数据不会写入正式数据库</div>"
        f"<header><div><h1>{_e(title)}</h1><p>新来源与 WHO xMart 分开显示；不跨来源相加，不静默覆盖。</p></div>"
        "<form method='post' action='/logout'><button>退出</button></form></header><main>"
        f"{body}</main></body></html>"
    )


def _login(error: str = "") -> HTMLResponse:
    return _layout(
        "P1-9 临时 Dashboard 登录",
        f"<section class='card'><p>{_e(error)}</p><form method='post' action='/login'>"
        "<label>用户名<input name='username' autocomplete='username' required></label>"
        "<label>密码<input name='password' type='password' autocomplete='current-password' required></label>"
        "<button>登录</button></form></section>",
    )


def _publications(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    """SELECT pub.id publication_id,pub.snapshot_id,pub.data_source_id,pub.observation_count,
                    ds.display_name source_name,ds.authority publisher,sv.artifact_sha256,
                    sv.license_status,mc.metric_code,mc.metric_display_name,mc.data_nature,
                    mc.training_eligible,mc.geography_semantics,s.content_sha256 snapshot_sha256
                    FROM epidemic_discovery.p1_9_publications pub
                    JOIN epidemic.data_sources ds ON ds.id=pub.data_source_id
                    JOIN epidemic_discovery.p1_9_source_versions sv ON sv.id=pub.source_version_id
                    JOIN epidemic_discovery.p1_9_mapping_contracts mc ON mc.id=sv.mapping_contract_id
                    JOIN epidemic.dataset_snapshots s ON s.id=pub.snapshot_id
                    ORDER BY pub.published_at DESC"""
                )
            ).mappings()
        ]


def _observations(
    engine: Engine,
    publication_id: uuid.UUID,
    start: str | None,
    end: str | None,
    state_code: str | None,
    municipality_code: str | None,
) -> list[dict[str, Any]]:
    where = ["pub.id=:publication"]
    params: dict[str, object] = {"publication": publication_id}
    if start:
        where.append("o.period_start>=CAST(:start AS date)")
        params["start"] = start
    if end:
        where.append("o.period_start<=CAST(:end AS date)")
        params["end"] = end
    if state_code:
        where.append("o.dimensions->>'state_code'=:state")
        params["state"] = state_code
    if municipality_code:
        where.append("o.dimensions->>'municipality_code'=:municipality")
        params["municipality"] = municipality_code
    sql = (
        """SELECT o.id observation_id,o.period_start,o.value_numeric,o.metric_code,
      o.dimensions->>'state_code' state_code,o.dimensions->>'municipality_code' municipality_code,
      o.dimensions->>'geography_semantics' geography_semantics,
      o.dimensions->>'data_nature' data_nature,o.dimensions->>'training_eligible' training_eligible,
      o.dimensions->>'quality_status' quality_status,l.source_row_set_sha256,l.source_row_count,
      pub.id publication_id,pub.snapshot_id
      FROM epidemic_discovery.p1_9_publications pub
      JOIN epidemic.dataset_snapshot_observations so ON so.snapshot_id=pub.snapshot_id
      JOIN epidemic.normalized_observations o ON o.id=so.observation_id
      JOIN epidemic_discovery.p1_9_observation_lineage l ON l.observation_id=o.id
      WHERE """
        + " AND ".join(where)
        + " ORDER BY o.period_start,state_code,municipality_code,o.id"
    )
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def create_app() -> FastAPI:
    engine = create_engine(
        os.environ["DASHBOARD_DATABASE_URL"],
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=5000"},
    )
    auth_store = Auth(Path(os.environ["DASHBOARD_AUTH_FILE"]))
    sessions = Sessions()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        with engine.connect() as connection:
            marker = connection.execute(
                text(
                    "SELECT 1 FROM epidemic_discovery.p1_9_environment_markers "
                    "WHERE environment_type='p1_9_demo' AND database_name=current_database() "
                    "AND disposable AND production_write_forbidden"
                )
            ).scalar_one_or_none()
            database_name = connection.execute(text("SELECT current_database()")).scalar_one()
        if marker != 1 or "p1_9_demo" not in str(database_name):
            raise RuntimeError("DEMO_DATABASE_GATE_FAILED")
        yield
        engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            }
        )
        return response

    def allowed(request: Request) -> bool:
        return sessions.valid(request.cookies.get(COOKIE))

    @app.get("/healthz")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/login")
    def login_page() -> HTMLResponse:
        return _login()

    @app.post("/login")
    async def login(request: Request) -> Response:
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        if not auth_store.verify(str(values.get("username", [""])[0]), str(values.get("password", [""])[0])):
            return _login("登录失败")
        response = RedirectResponse("/", 303)
        response.set_cookie(
            COOKIE, sessions.create(), max_age=SESSION_SECONDS, httponly=True, samesite="strict", path="/"
        )
        return response

    @app.post("/logout")
    def logout() -> Response:
        response = RedirectResponse("/login", 303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/")
    def dashboard(
        request: Request,
        publication: uuid.UUID | None = None,
        start: str | None = None,
        end: str | None = None,
        state_code: str | None = None,
        municipality_code: str | None = None,
    ) -> Response:
        if not allowed(request):
            return RedirectResponse("/login", 303)
        publications = _publications(engine)
        if not publications:
            return _layout("P1-9 临时 Dashboard", "<section class='card'><p>尚无已发布的临时 Snapshot。</p></section>")
        selected = next((item for item in publications if item["publication_id"] == publication), publications[0])
        rows = _observations(engine, selected["publication_id"], start, end, state_code, municipality_code)
        dates = [item["period_start"] for item in rows]
        values = [Decimal(item["value_numeric"]) for item in rows]
        state_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
        municipality_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
        date_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
        for item in rows:
            state_totals[str(item["state_code"])] += Decimal(item["value_numeric"])
            municipality_totals[str(item["municipality_code"])] += Decimal(item["value_numeric"])
            date_totals[str(item["period_start"])] += Decimal(item["value_numeric"])
        maximum = max([*date_totals.values(), Decimal(1)])
        date_bars = "".join(
            f"<div class='bar-label'><span>{_e(date)}</span><b>{_e(value)}</b></div>"
            f"<div class='bar'><span style='width:{float(value / maximum) * 100:.2f}%'></span></div>"
            for date, value in sorted(date_totals.items())
        )
        geography_rows = "".join(
            f"<tr><td>{_e(code)}</td><td>{_e(total)}</td></tr>" for code, total in sorted(municipality_totals.items())
        )
        detail_rows = "".join(
            f"<tr><td>{_e(item['period_start'])}</td><td>{_e(item['state_code'])}</td>"
            f"<td>{_e(item['municipality_code'])}</td><td>{_e(item['value_numeric'])}</td>"
            f"<td>{_e(item['quality_status'])}</td><td><a href='/lineage/{_e(item['observation_id'])}'>追踪</a></td></tr>"
            for item in rows
        )
        options = "".join(
            f"<option value='{_e(item['publication_id'])}' {'selected' if item == selected else ''}>{_e(item['source_name'])}</option>"
            for item in publications
        )
        summary = f"""<section class='card summary'><div class='stat'><b>来源</b><br>{_e(selected["source_name"])}</div>
        <div class='stat'><b>发布者</b><br>{_e(selected["publisher"])}</div><div class='stat'><b>指标</b><br>{_e(selected["metric_display_name"])}</div>
        <div class='stat'><b>性质</b><br>{_e(selected["data_nature"])}</div><div class='stat'><b>training_eligible</b><br>{_e(selected["training_eligible"])}</div>
        <div class='stat'><b>地理粒度</b><br>通知地点州/市代码</div><div class='stat'><b>最早/最晚</b><br>{_e(min(dates) if dates else None)} / {_e(max(dates) if dates else None)}</div>
        <div class='stat'><b>Observation</b><br>{_e(len(rows))}</div><div class='stat'><b>Snapshot</b><br>{_e(str(selected["snapshot_id"])[:12])}</div>
        <div class='stat'><b>Artifact</b><br>{_e(str(selected["artifact_sha256"])[:12])}</div></section>"""
        filters = f"""<section class='card'><form class='filters' method='get'><label>国家<select disabled><option>Brazil</option></select></label>
        <label>病种<select disabled><option>Dengue</option></select></label><label>数据来源<select name='publication'>{options}</select></label>
        <label>指标<select disabled><option>登革热通知记录数</option></select></label><label>开始日期（可空）<input type='date' name='start' value='{_e(start or "")}'></label>
        <label>结束日期（可空）<input type='date' name='end' value='{_e(end or "")}'></label><label>州代码<input name='state_code' value='{_e(state_code or "")}'></label>
        <label>市代码<input name='municipality_code' value='{_e(municipality_code or "")}'></label><button>应用筛选</button></form></section>"""
        panels = f"""<section class='panels'><article class='card'><h2>图1：按通知日期</h2>{date_bars}</article>
        <article class='card'><h2>图2：实际通知地</h2><table><thead><tr><th>市代码</th><th>通知记录数</th></tr></thead><tbody>{geography_rows}</tbody></table></article>
        <article class='card'><h2>图3：质量与训练资格</h2><p class='warning'>许可待人工复核；地理名称待验证；这是通知记录的确定性聚合，不是确诊病例。</p>
        <p><span class='pill'>derived_aggregation</span> <span class='pill'>training_eligible=false</span></p>
        <p>当前筛选总通知记录：<strong>{_e(sum(values, Decimal(0)))}</strong></p><p>州代码数：{_e(len(state_totals))}；市代码数：{_e(len(municipality_totals))}</p></article></section>"""
        table = f"""<section class='card'><h2>与图表同一 Snapshot、同一筛选的明细表</h2><div class='scroll'><table><thead><tr><th>通知日期</th>
        <th>州代码</th><th>市代码</th><th>通知记录数</th><th>质量</th><th>血缘</th></tr></thead><tbody>{detail_rows}</tbody></table></div></section>"""
        return _layout(
            "P1-9 临时疫情三联图 Dashboard",
            filters
            + summary
            + "<p class='warning'>时间条件为空时展示该来源在所选不可变 Snapshot 中的全部数据。</p>"
            + panels
            + table,
        )

    @app.get("/lineage/{observation_id}")
    def lineage(observation_id: uuid.UUID, request: Request) -> Response:
        if not allowed(request):
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """SELECT o.id observation_id,o.period_start,o.value_numeric,o.dimensions,l.source_row_set_sha256,
                    l.source_row_count,l.artifact_sha256,l.promotion_run_id,l.mapping_contract_id,
                    l.candidate_version_id,pub.id publication_id,pub.snapshot_id,pub.normalization_run_id
                    FROM epidemic.normalized_observations o
                    JOIN epidemic_discovery.p1_9_observation_lineage l ON l.observation_id=o.id
                    JOIN epidemic_discovery.p1_9_publications pub ON pub.promotion_run_id=l.promotion_run_id
                    WHERE o.id=:id"""
                    ),
                    {"id": observation_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return _layout("血缘不存在", "<section class='card'>未找到记录。</section>")
        safe = dict(row)
        return _layout(
            "P1-9 Observation 血缘",
            "<section class='card'><p>该单元格可追溯到 Promotion、映射契约、候选版本、Artifact 与源行集合哈希；不显示敏感原始字段。</p>"
            f"<pre>{_e(json.dumps(safe, ensure_ascii=False, default=str, indent=2))}</pre></section>",
        )

    return app

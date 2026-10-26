# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import Engine, create_engine, text

SESSION_SECONDS = 8 * 60 * 60
MAX_POINTS = 5000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
COOKIE_NAME = "epidemic_dashboard_session"


class AuthStore:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.username = str(raw.get("username", "analyst"))
        self.salt = bytes.fromhex(str(raw["salt_hex"]))
        self.expected = bytes.fromhex(str(raw["hash_hex"]))
        self.n = int(raw.get("n", 16384))
        self.r = int(raw.get("r", 8))
        self.p = int(raw.get("p", 1))

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
        if not token:
            return False
        expires = self.sessions.get(token)
        if expires is None or expires <= time.time():
            self.sessions.pop(token, None)
            return False
        return True

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.time() + SESSION_SECONDS
        return token

    def revoke(self, token: str | None) -> None:
        if token:
            self.sessions.pop(token, None)

    def allowed(self, key: str) -> bool:
        return self.locked_until.get(key, 0) <= time.time()

    def failed(self, key: str) -> None:
        now = time.time()
        events = self.failures[key]
        while events and events[0] < now - 10 * 60:
            events.popleft()
        events.append(now)
        if len(events) >= 5:
            self.locked_until[key] = now + 5 * 60

    def succeeded(self, key: str) -> None:
        self.failures.pop(key, None)
        self.locked_until.pop(key, None)


def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime, uuid.UUID, Decimal)):
        return str(value)
    return value


def _row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row.items()}


def _json_response(value: Any, status_code: int = 200) -> JSONResponse:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_RESPONSE_BYTES:
        return JSONResponse({"detail": "RESPONSE_TOO_LARGE"}, status_code=413)
    return JSONResponse(value, status_code=status_code)


def _strict_date(value: str | None) -> dt.date | None:
    if value is None or len(value) != 10:
        return None
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "local"


def _authorized(request: Request) -> bool:
    store: SessionStore = request.app.state.sessions
    return store.authenticated(request.cookies.get(COOKIE_NAME))


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "AUTHENTICATION_REQUIRED"}, status_code=401)


def _query(engine: Engine, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        rows = connection.execute(text(sql), dict(params or {})).mappings().all()
    return [_row(dict(row)) for row in rows]


LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>疫情仪表板登录</title><style nonce="{nonce}">body{{font-family:system-ui,sans-serif;background:#f2f5f7;margin:0;display:grid;place-items:center;min-height:100vh}}main{{background:white;padding:2rem;border-radius:14px;box-shadow:0 8px 30px #17324d22;width:min(88vw,360px)}}label,input,button{{display:block;width:100%;box-sizing:border-box}}input,button{{padding:.75rem;margin:.4rem 0 1rem}}button{{background:#075985;color:white;border:0;border-radius:8px}}.error{{color:#9f1239}}</style></head><body><main><h1>疫情数据仪表板</h1><p>请输入本地保存的访问凭据。</p>{error}<form method="post" action="/login" autocomplete="off"><label for="username">用户名</label><input id="username" name="username" required maxlength="64"><label for="password">密码</label><input id="password" name="password" type="password" required maxlength="256"><button type="submit">登录</button></form></main></body></html>"""


DASHBOARD_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>只读疫情数据三联图</title><style nonce="{nonce}">:root{{color-scheme:light;--ink:#102a43;--muted:#526777;--blue:#0369a1;--orange:#c2410c;--warn:#b45309;--fail:#be123c;--pass:#15803d}}*{{box-sizing:border-box}}body{{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#f4f7f9;color:var(--ink)}}header,main{{max-width:1280px;margin:auto;padding:1rem}}header{{display:flex;justify-content:space-between;align-items:center}}button,select,input{{font:inherit;padding:.55rem;border:1px solid #aab8c2;border-radius:7px;background:white}}button{{cursor:pointer}}.notice{{background:#e0f2fe;border-left:5px solid var(--blue);padding:.8rem;margin:.5rem 0}}.selectors{{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:.7rem;background:white;padding:1rem;border-radius:12px}}label{{display:grid;gap:.3rem;font-weight:600}}.meta{{display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem;margin:1rem 0}}.card,.chart{{background:white;border-radius:12px;padding:1rem;box-shadow:0 2px 10px #17324d12}}.charts{{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}}svg{{width:100%;height:260px;border:1px solid #dbe4ea}}.legend button{{border:2px solid transparent;margin:.2rem}}.legend button:focus{{border-color:#111}}table{{border-collapse:collapse;width:100%;font-size:.86rem}}th,td{{border-bottom:1px solid #dbe4ea;padding:.4rem;text-align:left}}.table-wrap{{overflow:auto;max-height:320px}}.sr{{position:absolute;left:-10000px}}@media(max-width:850px){{.selectors,.meta,.charts{{grid-template-columns:1fr}}header{{align-items:flex-start}}}}</style></head><body><header><div><h1>WHO 疫情数据三联图</h1><p>只读、已持久化数据</p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><main><p class="notice">此处仅筛选已抓取并持久化的数据，不会重新发起网络抓取。</p><p class="notice">当前仅展示报告期数据，尚未执行疫情预测或爆发时间计算。</p><section class="selectors" aria-label="数据筛选"><label>国家<select id="country"></select></label><label>病种<select id="disease"></select></label><label>snapshot<select id="snapshot"></select></label><label>显示开始日期<input id="start" type="date"></label><label>显示结束日期<input id="end" type="date"></label></section><section id="meta" class="meta" aria-label="只读元数据"></section><section class="charts"><article class="chart"><h2>新增报告病例趋势</h2><div class="legend"><button type="button" data-metric="cases_new">病例（线）</button></div><svg id="cases" role="img" aria-label="新增报告病例趋势图"></svg><div id="cases-table" class="table-wrap"></div></article><article class="chart"><h2>新增报告死亡趋势</h2><div class="legend"><button type="button" data-metric="deaths_new">死亡（线）</button></div><p id="death-empty" hidden>该数据源未提供这一时期的死亡指标，不等于死亡数为 0。</p><svg id="deaths" role="img" aria-label="新增报告死亡趋势图"></svg><div id="deaths-table" class="table-wrap"></div></article><article class="chart"><h2>数据质量与完整性</h2><div class="legend"><button type="button">✓ pass</button><button type="button">⚠ warning</button><button type="button">✕ fail</button></div><svg id="quality" role="img" aria-label="各报告期数据质量与完整性图"></svg><div id="quality-table" class="table-wrap"></div></article></section><p>数据署名：World Health Organization 及底层报告国家。<a id="terms" href="#" rel="noreferrer">WHO 数据条款</a>。不代表 WHO 对本系统的认可或背书。</p></main><script nonce="{nonce}">"use strict";const ns="http://www.w3.org/2000/svg";const $=id=>document.getElementById(id);const state={{snapshots:[]}};function clear(n){{while(n.firstChild)n.removeChild(n.firstChild)}}function txt(tag,value,cls){{const n=document.createElement(tag);n.textContent=String(value);if(cls)n.className=cls;return n}}function option(value,label){{const n=document.createElement("option");n.value=value;n.textContent=label;return n}}async function api(url){{const r=await fetch(url,{{credentials:"same-origin",headers:{{"Accept":"application/json"}}}});if(r.status===401){{location.href="/login";throw new Error("auth")}}if(!r.ok)throw new Error("request");return r.json()}}function line(svg,points,color){{let current=null;for(const p of points){{if(p.value===null){{current=null;continue}}if(!current){{current=document.createElementNS(ns,"polyline");current.setAttribute("fill","none");current.setAttribute("stroke",color);current.setAttribute("stroke-width","2");current.setAttribute("points","");svg.appendChild(current)}}const old=current.getAttribute("points");current.setAttribute("points",old+(old?" ":"")+p.x+","+p.y)}}}}function table(target,headers,rows){{clear(target);const t=document.createElement("table"),head=document.createElement("thead"),tr=document.createElement("tr");headers.forEach(h=>tr.appendChild(txt("th",h)));head.appendChild(tr);t.appendChild(head);const body=document.createElement("tbody");rows.slice(0,100).forEach(row=>{{const r=document.createElement("tr");row.forEach(v=>r.appendChild(txt("td",v===null?"缺失":v)));body.appendChild(r)}});t.appendChild(body);target.appendChild(t)}}function trend(id,metric,payload,color){{const svg=$(id);clear(svg);svg.setAttribute("viewBox","0 0 600 260");const periods=payload.quality.map(q=>q.period_start);const by=new Map(payload.points.filter(p=>p.metric_code===metric).map(p=>[p.period_start+"|"+p.granularity,p]));const values=payload.points.filter(p=>p.metric_code===metric).map(p=>Number(p.value_numeric));const min=Math.min(0,...values),max=Math.max(1,...values);if(min<0){{const y=230-(0-min)/(max-min)*200;const z=document.createElementNS(ns,"line");z.setAttribute("x1","35");z.setAttribute("x2","585");z.setAttribute("y1",String(y));z.setAttribute("y2",String(y));z.setAttribute("stroke","#555");z.setAttribute("stroke-dasharray","4 3");svg.appendChild(z)}}const groups=new Map();payload.quality.forEach((q,i)=>{{const key=q.granularity;if(!groups.has(key))groups.set(key,[]);const p=by.get(q.period_start+"|"+key);groups.get(key).push({{x:35+i*Math.max(1,550/Math.max(1,periods.length-1)),y:p?230-(Number(p.value_numeric)-min)/(max-min)*200:0,value:p?Number(p.value_numeric):null,date:q.period_start,granularity:key}})}});for(const g of groups.values())line(svg,g,color);const rows=[];for(const g of groups.values())for(const p of g)rows.push([p.date,p.granularity,p.value]);table($(id+"-table"),["报告期","粒度","数值"],rows);if(metric==="deaths_new")$("death-empty").hidden=values.length>0}}function quality(payload){{const svg=$("quality");clear(svg);svg.setAttribute("viewBox","0 0 600 260");const n=Math.max(1,payload.quality.length),w=520/n;payload.quality.forEach((q,i)=>{{let y=230;[["pass_count","#15803d","pass"],["warning_count","#b45309","warning"],["fail_count","#be123c","fail"]].forEach(x=>{{const h=Number(q[x[0]])*18;if(h){{const r=document.createElementNS(ns,"rect");r.setAttribute("x",String(40+i*w));r.setAttribute("y",String(y-h));r.setAttribute("width",String(Math.max(2,w-2)));r.setAttribute("height",String(h));r.setAttribute("fill",x[1]);r.setAttribute("aria-label",q.period_start+" "+x[2]+" "+q[x[0]]);const title=document.createElementNS(ns,"title");title.textContent=JSON.stringify(q.quality_flags||[]);r.appendChild(title);svg.appendChild(r);y-=h}}}})}});table($("quality-table"),["报告期","粒度","pass","warning","fail","缺失预期指标","可训练"],payload.quality.map(q=>[q.period_start,q.granularity,q.pass_count,q.warning_count,q.fail_count,q.expected_metric_status==="unknown"?"unknown":q.missing_expected_metric_count,q.default_train_eligible_count]))}}function metadata(s){{const m=$("meta");clear(m);[["数据源",s.source_key],["国家 / 病种",s.country_name+" / "+s.disease_slug],["请求范围",s.requested_start+" — "+s.requested_end],["数据粒度",s.granularities.join(", ")],["snapshot 创建",s.snapshot_created_at],["schema / contract",s.snapshot_schema_version+" / "+s.contract_version],["artifact SHA",s.artifact_sha12],["观测 / warning",s.observation_count+" / "+s.warning_count]].forEach(x=>{{const c=txt("div",x[0]+"："+x[1],"card");m.appendChild(c)}});$("terms").href=s.terms_url}}async function loadSeries(){{const id=$("snapshot").value;if(!id)return;const qs=new URLSearchParams();if($("start").value)qs.set("start_date",$("start").value);if($("end").value)qs.set("end_date",$("end").value);const data=await api("/api/v1/snapshots/"+encodeURIComponent(id)+"/series?"+qs);metadata(data.snapshot);trend("cases","cases_new",data,"#0369a1");trend("deaths","deaths_new",data,"#c2410c");quality(data)}}function refreshSnapshots(){{clear($("snapshot"));state.snapshots.filter(s=>(!$("country").value||s.country_alpha2===$("country").value)&&(!$("disease").value||s.disease_slug===$("disease").value)).forEach(s=>$("snapshot").appendChild(option(s.snapshot_id,s.requested_start+" — "+s.requested_end)));loadSeries()}}async function init(){{const o=await api("/api/v1/options"),s=await api("/api/v1/snapshots?page=1&page_size=100");state.snapshots=s.items;o.countries.forEach(x=>$("country").appendChild(option(x.country_alpha2,x.country_name)));o.diseases.forEach(x=>$("disease").appendChild(option(x.disease_slug,x.disease_slug)));$("country").addEventListener("change",refreshSnapshots);$("disease").addEventListener("change",refreshSnapshots);$("snapshot").addEventListener("change",loadSeries);$("start").addEventListener("change",loadSeries);$("end").addEventListener("change",loadSeries);refreshSnapshots()}}init();</script></body></html>"""


DASHBOARD_HTML = DASHBOARD_HTML.replace(
    'function refreshSnapshots(){{clear($("snapshot"));state.snapshots.filter(s=>(!$("country").value||s.country_alpha2===$("country").value)&&(!$("disease").value||s.disease_slug===$("disease").value)).forEach(s=>$("snapshot").appendChild(option(s.snapshot_id,s.requested_start+" — "+s.requested_end)));loadSeries()}}',
    'function refreshSnapshots(){{clear($("snapshot"));let matches=state.snapshots.filter(s=>(!$("country").value||s.country_alpha2===$("country").value)&&(!$("disease").value||s.disease_slug===$("disease").value));if(!matches.length&&state.snapshots.length){{const fallback=state.snapshots.find(s=>s.country_alpha2===$("country").value)||state.snapshots[0];$("country").value=fallback.country_alpha2;$("disease").value=fallback.disease_slug;matches=[fallback]}}matches.forEach(s=>$("snapshot").appendChild(option(s.snapshot_id,s.requested_start+" — "+s.requested_end)));loadSeries()}}',
).replace(
    'state.snapshots=s.items;o.countries.forEach(x=>$("country").appendChild(option(x.country_alpha2,x.country_name)));o.diseases.forEach(x=>$("disease").appendChild(option(x.disease_slug,x.disease_slug)));',
    'state.snapshots=s.items;o.countries.forEach(x=>$("country").appendChild(option(x.country_alpha2,x.country_name)));o.diseases.forEach(x=>$("disease").appendChild(option(x.disease_slug,x.disease_slug)));if(state.snapshots.length){{$("country").value=state.snapshots[0].country_alpha2;$("disease").value=state.snapshots[0].disease_slug}}',
)

# Keep all DOM writes text-only while presenting frozen server-side watermark evidence.
DASHBOARD_HTML = DASHBOARD_HTML.replace(
    "</script>",
    '''function metadata(s){{const m=$("meta");clear(m);const ratio=s.coverage_ratio===null?"UNKNOWN":(Number(s.coverage_ratio)*100).toFixed(1)+"%";[["data window",s.requested_start+" - "+s.requested_end],["official data through",s.source_data_through||"UNKNOWN"],["cases data through",s.cases_new_data_through||"NO_DATA"],["deaths data through",s.deaths_new_data_through||"NO_DATA (not zero)"],["retrieved at",s.retrieved_at||s.snapshot_created_at],["reporting lag days",s.reporting_lag_days===null?"UNKNOWN":s.reporting_lag_days],["system freshness",s.freshness_status||"UNKNOWN"],["coverage",ratio],["observed / expected / missing",s.observed_period_count+" / "+s.expected_period_count+" / "+s.missing_period_count],["source",s.source_key],["snapshot ID",s.snapshot_id],["artifact SHA",s.artifact_sha12]].forEach(x=>m.appendChild(txt("div",x[0]+": "+x[1],"card")));$("terms").href=s.terms_url}}setTimeout(()=>{{const sid=new URLSearchParams(location.search).get("snapshot");if(sid&&/^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$/i.test(sid)&&state.snapshots.some(s=>s.snapshot_id===sid)){{const target=state.snapshots.find(s=>s.snapshot_id===sid);$("country").value=target.country_alpha2;$("disease").value=target.disease_slug;refreshSnapshots();$("snapshot").value=sid;loadSeries()}}}},500);</script>''',
)


def create_app() -> FastAPI:
    database_url = os.environ["DASHBOARD_DATABASE_URL"]
    auth_file = Path(os.environ["DASHBOARD_AUTH_FILE"])
    engine = create_engine(
        database_url,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=5000"},
        hide_parameters=True,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = engine
        app.state.auth = AuthStore(auth_file)
        app.state.sessions = SessionStore()
        yield
        engine.dispose()

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def secure_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        nonce = getattr(request.state, "nonce", "")
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        _query(request.app.state.engine, "SELECT 1 AS ready")
        return _json_response({"status": "ready"})

    @app.get("/login")
    async def login_page(request: Request) -> HTMLResponse:
        request.state.nonce = secrets.token_urlsafe(18)
        return HTMLResponse(LOGIN_HTML.format(nonce=request.state.nonce, error=""))

    @app.post("/login")
    async def login(request: Request) -> Response:
        request.state.nonce = secrets.token_urlsafe(18)
        key = _client_key(request)
        sessions: SessionStore = request.app.state.sessions
        if not sessions.allowed(key):
            return HTMLResponse(
                LOGIN_HTML.format(nonce=request.state.nonce, error='<p class="error">登录暂时不可用，请稍后重试。</p>'),
                status_code=429,
            )
        body = await request.body()
        if len(body) > 4096:
            return HTMLResponse(
                LOGIN_HTML.format(nonce=request.state.nonce, error='<p class="error">登录失败。</p>'), status_code=401
            )
        values = urllib.parse.parse_qs(body.decode(errors="ignore"), strict_parsing=False)
        username = values.get("username", [""])[0]
        password = values.get("password", [""])[0]
        auth: AuthStore = request.app.state.auth
        if not auth.verify(username, password):
            sessions.failed(key)
            status = 429 if not sessions.allowed(key) else 401
            return HTMLResponse(
                LOGIN_HTML.format(nonce=request.state.nonce, error='<p class="error">登录失败。</p>'),
                status_code=status,
            )
        sessions.succeeded(key)
        token = sessions.create()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        request.app.state.sessions.revoke(request.cookies.get(COOKIE_NAME))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/")
    async def dashboard(request: Request) -> Response:
        if not _authorized(request):
            return RedirectResponse("/login", status_code=303)
        request.state.nonce = secrets.token_urlsafe(18)
        return HTMLResponse(DASHBOARD_HTML.format(nonce=request.state.nonce))

    @app.get("/api/v1/options")
    async def options(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        engine: Engine = request.app.state.engine
        countries = _query(
            engine,
            "SELECT DISTINCT country_alpha2,country_name FROM epidemic.dashboard_snapshot_catalog_v1 ORDER BY country_name,country_alpha2",
        )
        diseases = _query(
            engine, "SELECT DISTINCT disease_slug FROM epidemic.dashboard_snapshot_catalog_v1 ORDER BY disease_slug"
        )
        return _json_response({"countries": countries, "diseases": diseases})

    @app.get("/api/v1/snapshots")
    async def snapshots(request: Request, page: int = 1, page_size: int = 50) -> Response:
        if not _authorized(request):
            return _unauthorized()
        if page < 1 or page_size < 1 or page_size > 100:
            return _json_response({"detail": "INVALID_PAGINATION"}, 400)
        engine: Engine = request.app.state.engine
        offset = (page - 1) * page_size
        count = _query(engine, "SELECT count(*) AS total FROM epidemic.dashboard_snapshot_catalog_v1")[0]["total"]
        rows = _query(
            engine,
            "SELECT * FROM epidemic.dashboard_snapshot_catalog_v1 ORDER BY source_data_through DESC NULLS LAST,snapshot_created_at DESC,snapshot_id LIMIT :limit OFFSET :offset",
            {"limit": page_size, "offset": offset},
        )
        return _json_response({"page": page, "page_size": page_size, "total": count, "items": rows})

    @app.get("/api/v1/snapshots/{snapshot_uuid}/series")
    async def series(
        request: Request, snapshot_uuid: str, start_date: str | None = None, end_date: str | None = None
    ) -> Response:
        if not _authorized(request):
            return _unauthorized()
        try:
            sid = uuid.UUID(snapshot_uuid)
        except ValueError:
            return _json_response({"detail": "INVALID_SNAPSHOT_UUID"}, 400)
        start = _strict_date(start_date) if start_date is not None else None
        end = _strict_date(end_date) if end_date is not None else None
        if (
            (start_date is not None and start is None)
            or (end_date is not None and end is None)
            or (start and end and start > end)
        ):
            return _json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        engine: Engine = request.app.state.engine
        catalog = _query(
            engine, "SELECT * FROM epidemic.dashboard_snapshot_catalog_v1 WHERE snapshot_id=:sid", {"sid": sid}
        )
        if not catalog:
            return _json_response({"detail": "SNAPSHOT_NOT_FOUND"}, 404)
        params = {"sid": sid, "start": start, "end": end}
        where = "snapshot_id=:sid AND (CAST(:start AS date) IS NULL OR period_start>=CAST(:start AS date)) AND (CAST(:end AS date) IS NULL OR period_start<=CAST(:end AS date))"
        total = int(
            _query(
                engine, f"SELECT count(*) AS total FROM epidemic.dashboard_observation_series_v1 WHERE {where}", params
            )[0]["total"]
        )
        if total > MAX_POINTS:
            return _json_response({"detail": "POINT_LIMIT_EXCEEDED"}, 413)
        points = _query(
            engine,
            f"SELECT period_start,period_end,granularity,metric_code,value_numeric,unit,revision,quality_severity,quality_flags,default_train_eligible,source_date_type FROM epidemic.dashboard_observation_series_v1 WHERE {where} ORDER BY period_start,granularity,metric_code,revision",
            params,
        )
        quality = _query(
            engine,
            f"SELECT period_start,period_end,granularity,pass_count,warning_count,fail_count,default_train_eligible_count,missing_expected_metric_count,expected_metric_status,quality_flags FROM epidemic.dashboard_quality_summary_v1 WHERE {where} ORDER BY period_start,granularity",
            params,
        )
        return _json_response(
            {
                "snapshot": catalog[0],
                "points": points,
                "quality": quality,
                "point_count": total,
                "resampled": False,
                "interpolated": False,
                "predicted": False,
            }
        )

    return app

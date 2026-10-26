from __future__ import annotations
# ruff: noqa: E501

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import create_engine, text

COOKIE = "epidemic_source_review"
IDLE_SECONDS = 1_800
ABSOLUTE_SECONDS = 28_800
ORIGIN = "http://127.0.0.1:8021"

LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Source reviewer login</title></head>
<body><h1>Source reviewer login</h1>{error}<form method="post"><label>Username
<input name="username" autocomplete="username" required></label><label>Password
<input name="password" type="password" autocomplete="current-password" required></label>
<button>Login</button></form></body></html>"""

CONSOLE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Source Review Console</title><style nonce="{nonce}">body{{font-family:system-ui;margin:1rem;max-width:1400px}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}th,td{{border:1px solid #ccd;padding:.35rem;text-align:left}}
.warn{{color:#9a3412}}pre{{white-space:pre-wrap;overflow:auto}}label{{display:block;margin:.4rem 0}}</style></head><body>
<h1>Source Review Console</h1><p class="warn">Quarantine preview only. Nothing shown here is formal epidemic data.
Human approval is required.</p><div id="cases"></div><div id="detail"></div>
<form id="decision" hidden><h2>Human decision</h2><p>Approval creates one promotion job; it does not write observations.</p>
<label>Decision<select id="action"><option>APPROVED</option><option>REJECTED</option><option>CHANGES_REQUESTED</option></select></label>
<label>Scope<select id="scope"><option>APPROVE_ARTIFACT_ONCE</option><option>APPROVE_SOURCE_PROFILE</option></select></label>
<div id="checks"></div><label>Reason<input id="reason" maxlength="500" required></label>
<label>Re-enter reviewer password<input id="password" type="password" autocomplete="current-password" required></label>
<button id="submit" type="submit">批准当前版本并排队入库 / Submit decision</button></form><pre id="result"></pre>
<script nonce="{nonce}">const csrf={csrf};const cases=document.getElementById("cases"),detail=document.getElementById("detail"),form=document.getElementById("decision"),result=document.getElementById("result");let selected=null,etag=null;
const names=["publisher_confirmed","license_confirmed","aggregated_no_pii_confirmed","country_disease_confirmed","time_semantics_confirmed","measure_type_confirmed","unit_confirmed","mapping_confirmed","fixed_version_confirmed"];
function pre(x){{const e=document.createElement("pre");e.textContent=JSON.stringify(x,null,2);return e}}function button(label,fn){{const b=document.createElement("button");b.type="button";b.textContent=label;b.onclick=fn;return b}}
async function loadDetail(id,e){{selected=id;etag=e;detail.replaceChildren(pre(await (await fetch("/api/v1/review-cases/"+encodeURIComponent(id))).json()));form.hidden=false}}
fetch("/api/v1/review-cases"+(new URLSearchParams(location.search).get("run")?"?run="+encodeURIComponent(new URLSearchParams(location.search).get("run")):"")).then(r=>r.json()).then(x=>{{cases.appendChild(pre(x));for(const c of x.cases)cases.appendChild(button("Review "+c.review_case_id,()=>loadDetail(c.review_case_id,c.etag)))}}).catch(()=>location.href="/login");
const checks=document.getElementById("checks");for(const n of names){{const l=document.createElement("label"),i=document.createElement("input");i.type="checkbox";i.dataset.name=n;l.append(i,document.createTextNode(" Confirm "+n));checks.appendChild(l)}}
form.onsubmit=async e=>{{e.preventDefault();if(!selected)return;const body={{decision:document.getElementById("action").value,scope:document.getElementById("scope").value,reason:document.getElementById("reason").value,password:document.getElementById("password").value}};for(const i of checks.querySelectorAll("input"))body[i.dataset.name]=i.checked;const r=await fetch("/api/v1/review-cases/"+encodeURIComponent(selected)+"/decision",{{method:"POST",headers:{{"Content-Type":"application/json","X-CSRF-Token":csrf,"If-Match":etag,"Idempotency-Key":crypto.randomUUID()}},body:JSON.stringify(body)}});result.textContent=JSON.stringify(await r.json(),null,2)}};</script></body></html>"""


class Sessions:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[float, float, str, str]] = {}

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
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


def create_app() -> FastAPI:
    engine = create_engine(
        os.environ["REVIEW_DATABASE_URL"], connect_args={"options": "-c statement_timeout=5000"}, hide_parameters=True
    )
    auth_file = Path(os.environ["REVIEW_AUTH_FILE"])
    sessions = Sessions()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        nonce = getattr(request.state, "nonce", "")
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'",
                "Cache-Control": "no-store",
            }
        )
        return response

    def session(request: Request) -> tuple[str, str] | None:
        return sessions.get(request.cookies.get(COOKIE))

    @app.get("/healthz")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/login")
    def login_page() -> HTMLResponse:
        return HTMLResponse(LOGIN.format(error=""))

    @app.post("/login")
    async def login(request: Request):  # type: ignore[no-untyped-def]
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        username = str(values.get("username", [""])[0])
        password = str(values.get("password", [""])[0])
        if not _auth(auth_file, username, password):
            return HTMLResponse(LOGIN.format(error="<p>Invalid credentials</p>"), 401)
        token = sessions.create(username)
        response = RedirectResponse("/", 303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/", max_age=ABSOLUTE_SECONDS)
        return response

    @app.get("/")
    def root(request: Request):  # type: ignore[no-untyped-def]
        current = session(request)
        if not current:
            return RedirectResponse("/login", 303)
        request.state.nonce = secrets.token_urlsafe(18)
        return HTMLResponse(CONSOLE.format(nonce=request.state.nonce, csrf=json.dumps(current[1])))

    @app.get("/api/v1/review-cases")
    def cases(request: Request, run: uuid.UUID | None = None):  # type: ignore[no-untyped-def]
        if not session(request):
            return JSONResponse({"error": "UNAUTHORIZED"}, 401)
        clause = "WHERE discovery_run_id=:run" if run else ""
        with engine.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    text(f"SELECT * FROM epidemic_discovery.review_cases_v {clause} ORDER BY review_case_id"),
                    {"run": run} if run else {},
                ).mappings()
            ]
        return {"review_required": True, "formal_imported": False, "cases": rows}

    @app.get("/api/v1/review-cases/{case_id}")
    def detail(case_id: uuid.UUID, request: Request):  # type: ignore[no-untyped-def]
        if not session(request):
            return JSONResponse({"error": "UNAUTHORIZED"}, 401)
        tables = {}
        with engine.connect() as connection:
            for key, view in (("mapping_table", "review_mapping_v"), ("preview_table", "review_preview_v"), ("schema_table", "review_schema_v")):
                tables[key] = [dict(row) for row in connection.execute(text(f"SELECT * FROM epidemic_discovery.{view} WHERE review_case_id=:id"), {"id": case_id}).mappings()]
        return {**tables, "raw_path_exposed": False}

    @app.post("/api/v1/review-cases/{case_id}/decision")
    async def decision(case_id: uuid.UUID, request: Request):  # type: ignore[no-untyped-def]
        current = session(request)
        if not current:
            return JSONResponse({"error": "UNAUTHORIZED"}, 401)
        username, csrf = current
        referer = request.headers.get("referer", "")
        if request.headers.get("origin") != ORIGIN or not referer.startswith(ORIGIN + "/") or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            return JSONResponse({"error": "CSRF_REJECTED"}, 403)
        body = await request.json()
        confirmations = {
            "publisher_confirmed", "license_confirmed", "aggregated_no_pii_confirmed", "country_disease_confirmed",
            "time_semantics_confirmed", "measure_type_confirmed", "unit_confirmed", "mapping_confirmed", "fixed_version_confirmed",
        }
        if not all(body.get(name) is True for name in confirmations):
            return JSONResponse({"error": "CONFIRMATIONS_REQUIRED"}, 422)
        if not _auth(auth_file, username, str(body.get("password", ""))):
            return JSONResponse({"error": "RECENT_REAUTH_REQUIRED"}, 401)
        try:
            key = uuid.UUID(request.headers.get("idempotency-key", ""))
        except ValueError:
            return JSONResponse({"error": "IDEMPOTENCY_KEY_REQUIRED"}, 422)
        action = str(body.get("decision"))
        scope = str(body.get("scope", "APPROVE_ARTIFACT_ONCE"))
        etag = request.headers.get("if-match", "")
        with engine.begin() as connection:
            row = connection.execute(text("SELECT * FROM epidemic_discovery.review_cases_v WHERE review_case_id=:id"), {"id": case_id}).mappings().one()
            evidence = {
                name: str(row[name])
                for name in (
                    "candidate_version_id", "canonical_endpoint_sha256", "publisher_evidence_sha256", "terms_sha256",
                    "artifact_sha256", "schema_sha256", "mapping_sha256", "assessment_sha256", "security_report_sha256",
                )
            }
            evidence["etag"] = etag
            evidence["scope"] = scope
            evidence_sha = hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            decision_sha = hashlib.sha256(f"{case_id}|{key}|{action}|{evidence_sha}".encode()).hexdigest()
            connection.execute(
                text("SELECT epidemic_discovery.submit_review_decision(:case,:action,:scope,:reviewer,:reason,:key,:etag,CAST(:evidence AS jsonb),:evidence_sha,:decision_sha)"),
                {"case": case_id, "action": action, "scope": scope, "reviewer": username, "reason": str(body.get("reason", "")), "key": key, "etag": etag, "evidence": json.dumps(evidence), "evidence_sha": evidence_sha, "decision_sha": decision_sha},
            ).scalar_one()
        return {"state": action, "promotion_queued": False, "promotion_job_created_by": "source-promotion-worker", "formal_rows_written": False}

    return app

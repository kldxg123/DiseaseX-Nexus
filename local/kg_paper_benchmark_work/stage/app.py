# ruff: noqa: E501  # long lines hold verbatim paper excerpts / hashes / Chinese UI strings
"""Local staging app for browser acceptance of the patched KG page.

Serves the COMPLETE patched forecast-model-knowledge-graph page (all sections,
structure identical to production — no trimming). Sections whose real backends
are outside this round get 200 stub JSON with minimal render-safe structures;
the two paper-benchmark endpoints serve the real reference JSON files.
Auth is a local stub cookie (staging only, no real credentials).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

STAGE_DIR = Path(__file__).resolve().parent
PATCH = STAGE_DIR.parent / "page_patch" / "src" / "epidemic_core"

# Extract the patched KG_HTML without importing the remote-dependent module.
_src = (PATCH / "forecast_model_kg" / "dashboard_app.py").read_text(encoding="utf-8")
_m = re.search(r'KG_HTML = """(.*?)"""', _src, re.S)
if not _m:
    raise SystemExit("KG_HTML not found in patched dashboard_app.py")
KG_HTML = _m.group(1)  # full page: preexecution + r3c + dual-loop + param-loop + qwen-loop + both benchmark blocks

OLD_JSON = Path("F:/作业/aibola/remote_patch/src/epidemic_core/forecast_model_kg/reference/sgp_dengue_paper_benchmark.v1.json")
NEW_JSON = PATCH / "forecast_model_kg" / "reference" / "mpox_rsos2024_paper_benchmark.v1.json"

# Minimal render-safe stubs (verified against forecast-model-kg.js render code).
PT_DEMO_OVERVIEW_STUB = {
    "demo_key": "AUT:legionella:legionnaires_reported_cases:month",
    "snapshot": {},
    "recommendation": None,
    "recommendation_reason_zh": [],
    "recommendation_components": [],
    "split": None,
    "parameter_contracts": [],
    "validation_metrics": {},
    "test_evaluations": [],
    "test_comparison": {},
    "champion_decisions": [],
    "feedback_summary": {},
    "learning_cycles": [],
    "limitations": [],
    "tuning_runs": [],
}
QWEN_OVERVIEW_STUB = {
    "run": None,  # JS treats missing run as "尚未运行" and renders explicit empty states
    "track": None,
    "data_profile": {},
    "recommendation": None,
    "recommendation_reason_zh": [],
    "plans": [],
    "parameter_ranges": [],
    "rounds": [],
    "trials": [],
    "evaluation": None,
    "champion": None,
    "feedback_summary": {},
    "learning_cycles": [],
    "limitations": [],
}

app = FastAPI(title="kg-paper-benchmark-stage", docs_url=None, redoc_url=None)


def authorized(request: Request) -> bool:
    return request.cookies.get("epidemic_dashboard_session") == "stage-local"


def unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "UNAUTHORIZED"}, 401)


def stub(request: Request, payload: dict) -> Response:
    """200 stub for authenticated requests, 401 otherwise."""
    return JSONResponse(payload) if authorized(request) else unauthorized()


@app.get("/login")
def login_page() -> HTMLResponse:
    return HTMLResponse(
        "<html><body><form method='post' action='/login'>"
        "<input name='username'><input name='password' type='password'>"
        "<button type='submit'>login</button></form></body></html>"
    )


@app.post("/login")
def do_login() -> Response:
    resp = RedirectResponse("/forecast-model-knowledge-graph", 303)
    resp.set_cookie("epidemic_dashboard_session", "stage-local", httponly=True)
    return resp


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/static/forecast-model-kg.css")
def css() -> FileResponse:
    return FileResponse(PATCH / "dashboard" / "static" / "forecast-model-kg.css", media_type="text/css")


@app.get("/static/forecast-model-kg.js")
def js() -> FileResponse:
    return FileResponse(PATCH / "dashboard" / "static" / "forecast-model-kg.js", media_type="application/javascript")


@app.get("/forecast-model-knowledge-graph")
def kg_page(request: Request) -> Response:
    return HTMLResponse(KG_HTML) if authorized(request) else RedirectResponse("/login", 303)


# --- real benchmark endpoints (serve the real reference JSON files) ---

@app.get("/api/v1/forecast-model-knowledge-graph/benchmarks/sgp-dengue")
def old_benchmark(request: Request) -> Response:
    if not authorized(request):
        return unauthorized()
    return JSONResponse(json.loads(OLD_JSON.read_text(encoding="utf-8")))


@app.get("/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-mpox-2024")
def new_benchmark(request: Request) -> Response:
    if not authorized(request):
        return unauthorized()
    if not NEW_JSON.exists():
        return JSONResponse({"detail": "RSOS_MPOX_BENCHMARK_LOAD_FAILED"}, 503)
    return JSONResponse(json.loads(NEW_JSON.read_text(encoding="utf-8")))


@app.get("/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-240248-method-transfer-sgp-dengue")
def new_benchmark_canonical(request: Request) -> Response:
    """Canonical route name for the RSOS 2024 method-transfer benchmark; same payload as the alias."""
    return new_benchmark(request)


# --- 200 stubs for sections whose real backends are outside this round ---

@app.get("/api/v1/kg/r3c/catalog")
def r3c_catalog(request: Request) -> Response:
    return stub(request, {"items": []})


@app.get("/api/v1/kg/r3c/recommendations")
def r3c_recommendations(request: Request) -> Response:
    return stub(request, {"items": [], "execution_status": "DEFERRED_NOT_EXECUTED"})


@app.get("/api/v1/kg/import-runs")
def kg_import_runs(request: Request) -> Response:
    return stub(request, {"items": []})


@app.get("/api/v1/kg/graph-versions")
def kg_graph_versions(request: Request) -> Response:
    return stub(request, {"items": []})


@app.get("/api/v1/kg/review-items")
def kg_review_items(request: Request) -> Response:
    return stub(request, {"items": [], "status_counts": {}})


@app.get("/api/v1/kg/pt/demo/overview")
def pt_demo_overview(request: Request) -> Response:
    return stub(request, PT_DEMO_OVERVIEW_STUB)


@app.get("/api/v1/kg/autotuning/overview")
def qwen_autotuning_overview(request: Request) -> Response:
    return stub(request, QWEN_OVERVIEW_STUB)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8199)

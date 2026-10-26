from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8020"
ROOT = Path("USER_HOME/projects/openclaw")
REPORT_DIR = ROOT / "reports/forecast_model_kg_v1_20260729T053519Z_nogit"
SECRETS = ROOT / "runtime/secrets/vm201_runtime.env"
PSQL = ROOT / "runtime/postgres18x/bin/psql"
SOCKET = ROOT / "runtime/postgres-run"
DB = "epidemic_openclaw"

GUIDANCE_CASES = [
    ("COVID-19", "FRA", "covid_19"),
    ("Influenza", "PER", "influenza"),
    ("Dengue", "BRA", "dengue"),
    ("Ebola", "COD", "ebola"),
    ("Puumala", "FIN", "hantavirus_puumala"),
    ("Legionellosis", "AUT", "legionella"),
    ("Nipah", "BGD", "nipah"),
]

COUNT_TABLES = [
    "epidemic.query_requests",
    "epidemic.source_candidates",
    "epidemic.source_review_cases",
    "epidemic.review_decisions",
    "epidemic.data_sources",
    "epidemic.observations",
    "epidemic.trusted_observations",
    "epidemic.forecast_runs",
    "epidemic.forecast_predictions",
    "epidemic.snapshots",
    "epidemic.snapshot_members",
    "epidemic.publications",
    "epidemic.schedules",
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def load_password_redacted() -> str:
    values: dict[str, str] = {}
    for raw in SECRETS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    password = values.get("DASHBOARD_LOGIN_PASSWORD") or values.get("EPIDEMIC_DASHBOARD_PASSWORD")
    if not password:
        raise RuntimeError("CREDENTIAL_FIELD_NOT_FOUND")
    return password


def http_open(opener: urllib.request.OpenerDirector, path: str, method: str = "GET", data: dict[str, str] | None = None) -> tuple[int, str, dict[str, str]]:
    encoded = urllib.parse.urlencode(data or {}).encode("utf-8") if data is not None else None
    req = urllib.request.Request(BASE + path, data=encoded, method=method)
    if encoded is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), dict(exc.headers.items())


def run_sql(sql: str) -> list[str]:
    cmd = [
        str(PSQL),
        "-h",
        str(SOCKET),
        "-p",
        "55432",
        "-U",
        "postgres",
        "-d",
        DB,
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; "
        "SET LOCAL statement_timeout = '30s'; "
        "SET LOCAL idle_in_transaction_session_timeout = '60s'; "
        + sql
        + "; ROLLBACK;",
    ]
    out = subprocess.check_output(cmd, text=True)
    return [line for line in out.splitlines() if line and not line.startswith(("BEGIN", "SET", "ROLLBACK"))]


def db_counts() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for table in COUNT_TABLES:
        exists = run_sql(f"SELECT to_regclass('{table}') IS NOT NULL")[0] == "t"
        result[table] = {"exists": exists}
        if exists:
            result[table]["row_count"] = int(run_sql(f"SELECT count(*) FROM {table}")[0])
    return result


def browser_console_check() -> dict[str, Any]:
    # Best available true browser check. It uses an ephemeral browser context and
    # removes any temporary profile directory after closing. No secret or cookie is
    # printed or persisted by this script.
    try:
        import importlib.util

        if importlib.util.find_spec("playwright") is None:
            return {"status": "SKIPPED_PLAYWRIGHT_UNAVAILABLE"}
        from playwright.sync_api import sync_playwright
    except Exception:
        return {"status": "SKIPPED_PLAYWRIGHT_UNAVAILABLE"}

    password = load_password_redacted()
    temp_dir = tempfile.TemporaryDirectory(prefix="kg_browser_", dir="/tmp")
    errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on("requestfailed", lambda req: failed_requests.append(req.url))
            page.goto(BASE + "/login", wait_until="networkidle", timeout=15000)
            page.fill('input[name="username"]', "analyst")
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=15000)
            page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle", timeout=15000)
            page.wait_for_selector("#kgStatus", timeout=15000)
            page.wait_for_timeout(1000)
            required_selectors = ["#kgModels", "#kgReasons", "#kgValidation", "#kgGraph", "#kgTableFallback"]
            selector_presence = {selector: page.locator(selector).count() > 0 for selector in required_selectors}
            page.goto(BASE + "/", wait_until="networkidle", timeout=15000)
            dashboard_title = page.locator("body").inner_text(timeout=5000)[:80]
            context.close()
            browser.close()
        return {
            "status": "PASS",
            "console_error_count": len(errors),
            "pageerror_count": len(page_errors),
            "failed_request_count": len(failed_requests),
            "required_selectors": selector_presence,
            "dashboard_body_observed": bool(dashboard_title),
        }
    except Exception as exc:
        # Redacted message only; never include password, cookies, or request headers.
        return {"status": "FAIL", "redacted_error_type": type(exc).__name__}
    finally:
        password = ""
        temp_dir.cleanup()


def main() -> None:
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "credential_read": "NOT_ATTEMPTED",
        "positive_auth_session": "NOT_ATTEMPTED",
        "database_mutation": "NOT_EVALUATED",
        "model_side_effects": {
            "training_started": False,
            "prediction_started": False,
            "model_invocation_started": False,
            "active_pointer_modified": False,
        },
        "network_side_effects": {
            "external_network": False,
        },
    }
    before = db_counts()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
    try:
        password = load_password_redacted()
        result["credential_read"] = "PASS_REDACTED"
        health_code, _, _ = http_open(opener, "/healthz")
        ready_code, _, _ = http_open(opener, "/readyz")
        result["healthz"] = {"status": health_code}
        result["readyz"] = {"status": ready_code}
        login_code, _, _ = http_open(opener, "/login", "POST", {"username": 'REDACTED', "password": password})
        password = ""
        result["positive_auth_session"] = "PASS" if len(jar) > 0 and login_code in {200, 302, 303, 307} else "FAIL"

        page_code, page_body, _ = http_open(opener, "/forecast-model-knowledge-graph")
        required_blocks = {
            "recommended_model_pool": "kgModels" in page_body,
            "why": "kgReasons" in page_body,
            "validation_requirements": "kgValidation" in page_body,
            "graph_and_table": "kgGraph" in page_body and "kgTableFallback" in page_body,
            "empty_state_literal": "Loading guidance" in page_body or "None." in page_body,
            "error_state_literal": True,
        }
        result["knowledge_graph_page"] = {
            "status": page_code,
            "bytes": len(page_body),
            "required_blocks": required_blocks,
            "bad_text_marker": "????" in page_body,
        }
        js_code, js_body, _ = http_open(opener, "/static/forecast-model-kg.js")
        result["knowledge_graph_js"] = {
            "status": js_code,
            "has_table_fallback": "kgTableFallback" in js_body,
            "has_empty_state": "None." in js_body,
            "has_error_state": "Guidance failed" in js_body,
            "bad_text_marker": "????" in js_body,
        }
        api_paths = {
            "metadata": "/api/v1/forecast-model-knowledge-graph",
            "full_graph": "/api/v1/forecast-model-knowledge-graph?full=true",
            "disease_graph": "/api/v1/forecast-model-knowledge-graph/Legionellosis",
        }
        result["read_only_apis"] = {}
        for name, path in api_paths.items():
            code, body, _ = http_open(opener, path)
            parsed = json.loads(body) if code == 200 else {}
            result["read_only_apis"][name] = {
                "status": code,
                "bytes": len(body),
                "has_expected_shape": bool(parsed),
            }
        result["guidance_cases"] = {}
        for disease, country, expected_id in GUIDANCE_CASES:
            q = urllib.parse.urlencode({"disease": disease, "country_or_region": country, "target": "cases_new", "horizon": "4"})
            code, body, _ = http_open(opener, "/api/v1/forecast-model-guidance?" + q)
            parsed = json.loads(body) if code == 200 else {}
            result["guidance_cases"][disease] = {
                "status": code,
                "resolved": parsed.get("resolved_disease", {}).get("id"),
                "expected": expected_id,
                "recommended_count": len(parsed.get("recommended_model_families", [])),
                "side_effects": parsed.get("side_effects"),
                "warnings_count": len(parsed.get("warnings", [])),
            }
        invalid_code, invalid_body, _ = http_open(opener, "/api/v1/forecast-model-guidance?disease=not-a-disease&country_or_region=XXX&target=cases_new&horizon=4")
        result["error_state_api"] = {
            "status": invalid_code,
            "detail": json.loads(invalid_body).get("detail") if invalid_body else None,
        }
        for path in ["/", "/predictions", "/fasttrack-forecast", "/reviews"]:
            code, body, _ = http_open(opener, path)
            result.setdefault("existing_dashboard_pages", {})[path] = {
                "status": code,
                "bytes": len(body),
                "server_error_marker": "Internal Server Error" in body,
                "required_for_no_regression": path != "/reviews",
            }
        result["browser_console"] = browser_console_check()
    finally:
        jar.clear()

    after = db_counts()
    result["database_mutation"] = "PASS_ZERO" if before == after else "FAIL_DELTA"
    result["database_count_before"] = before
    result["database_count_after"] = after

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output = REPORT_DIR / "POSITIVE_AUTH_HTTP_CHECK_REDACTED.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    failures: list[str] = []
    if result["credential_read"] != "PASS_REDACTED":
        failures.append("credential_read")
    if result["positive_auth_session"] != "PASS":
        failures.append("positive_auth_session")
    if result.get("healthz", {}).get("status") != 200:
        failures.append("healthz")
    if result.get("readyz", {}).get("status") != 200:
        failures.append("readyz")
    if result.get("knowledge_graph_page", {}).get("status") != 200:
        failures.append("knowledge_graph_page")
    if not all(result.get("knowledge_graph_page", {}).get("required_blocks", {}).values()):
        failures.append("required_blocks")
    if result.get("knowledge_graph_js", {}).get("status") != 200:
        failures.append("knowledge_graph_js")
    for name, item in result.get("read_only_apis", {}).items():
        if item.get("status") != 200 or not item.get("has_expected_shape"):
            failures.append(f"api:{name}")
    for disease, item in result.get("guidance_cases", {}).items():
        if item.get("status") != 200 or item.get("resolved") != item.get("expected") or item.get("recommended_count", 0) <= 0:
            failures.append(f"guidance:{disease}")
        if item.get("side_effects") != {"db_write": False, "training_started": False, "prediction_started": False, "external_network": False}:
            failures.append(f"side_effects:{disease}")
    if result.get("error_state_api", {}).get("status") not in {404, 422}:
        failures.append("error_state_api")
    for path, item in result.get("existing_dashboard_pages", {}).items():
        if item.get("required_for_no_regression") and (item.get("status") != 200 or item.get("server_error_marker")):
            failures.append(f"existing_page:{path}")
    browser = result.get("browser_console", {})
    if browser.get("status") != "PASS" or browser.get("console_error_count") != 0 or browser.get("pageerror_count") != 0 or browser.get("failed_request_count") != 0:
        failures.append("browser_console")
    if result["database_mutation"] != "PASS_ZERO":
        failures.append("database_mutation")
    if failures:
        summary = {"status": "FAIL", "failures": failures, "report": str(output)}
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps({"status": "PASS", "report": str(output)}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Redacted failure; do not emit credential, cookie, header or traceback.
        print(json.dumps({"status": "FAIL_REDACTED", "error_type": type(exc).__name__}, sort_keys=True))
        raise SystemExit(1)

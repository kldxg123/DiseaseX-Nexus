from __future__ import annotations

import http.cookiejar
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8020"
ROOT = Path("USER_HOME/projects/openclaw")
REPORT_DIR = ROOT / "reports/forecast_model_kg_v1_20260729T053519Z_nogit"
SECRETS = ROOT / "runtime/secrets/vm201_runtime.env"


def load_password() -> str:
    values: dict[str, str] = {}
    for raw in SECRETS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    password = values.get("DASHBOARD_LOGIN_PASSWORD") or values.get("EPIDEMIC_DASHBOARD_PASSWORD")
    if not password:
        raise RuntimeError("DASHBOARD_PASSWORD_NOT_FOUND")
    return password


def open_url(opener: urllib.request.OpenerDirector, path: str, method: str = "GET", data: dict[str, str] | None = None) -> tuple[int, str]:
    encoded = urllib.parse.urlencode(data or {}).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=encoded, method=method)
    if encoded is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> None:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    result: dict[str, object] = {}
    for path in ["/healthz", "/readyz", "/login"]:
        code, body = open_url(opener, path)
        result[path] = {"status": code, "body_prefix": body[:120]}
    unauth_code, _ = open_url(opener, "/forecast-model-knowledge-graph")
    result["/forecast-model-knowledge-graph unauth"] = {"status": unauth_code}
    login_code, login_body = open_url(opener, "/login", "POST", {"username": 'REDACTED', "password": load_password()})
    result["/login POST"] = {"status": login_code, "set_cookie_count": len(jar), "body_prefix": login_body[:120]}
    checks = [
        "/forecast-model-knowledge-graph",
        "/static/forecast-model-kg.js",
        "/static/forecast-model-kg.css",
        "/api/v1/forecast-model-knowledge-graph",
        "/api/v1/forecast-model-knowledge-graph?full=true",
        "/api/v1/forecast-model-knowledge-graph/Legionellosis",
        "/api/v1/forecast-model-guidance?disease=COVID-19&country_or_region=FRA&target=cases_new&horizon=4",
        "/api/v1/forecast-model-guidance?disease=Influenza&country_or_region=PER&target=cases_new&horizon=4",
        "/api/v1/forecast-model-guidance?disease=Dengue&country_or_region=BRA&target=cases_new&horizon=4",
        "/api/v1/forecast-model-guidance?disease=Ebola&country_or_region=COD&target=cases_new&horizon=4",
        "/api/v1/forecast-model-guidance?disease=Puumala&country_or_region=FIN&target=cases_new&horizon=4",
        "/api/v1/forecast-model-guidance?disease=Legionellosis&country_or_region=AUT&target=cases_new&horizon=4",
        "/api/v1/forecast-model-guidance?disease=Nipah&country_or_region=BGD&target=cases_new&horizon=4",
    ]
    for path in checks:
        code, body = open_url(opener, path)
        item: dict[str, object] = {"status": code, "bytes": len(body), "has_bad_question_marks": "????" in body}
        if path.startswith("/api/") and code == 200:
            parsed = json.loads(body)
            if path.startswith("/api/v1/forecast-model-guidance"):
                item["resolved_disease"] = parsed["resolved_disease"]["id"]
                item["eligibility_track"] = parsed["eligibility_track"]
                item["side_effects"] = parsed["side_effects"]
                item["recommended_count"] = len(parsed["recommended_model_families"])
                item["warnings_count"] = len(parsed["warnings"])
            elif path.endswith("full=true"):
                item["node_count"] = len(parsed["nodes"])
                item["rule_count"] = len(parsed["rules"])
            elif "forecast-model-knowledge-graph/" in path:
                item["node_count"] = len(parsed["nodes"])
                item["rule_count"] = len(parsed["rules"])
            else:
                item["integrity"] = parsed["integrity"]
        result[path] = item
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output = REPORT_DIR / "LIVE_HTTP_AUTH_CHECK.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(output)
    failures = []
    for key, item in result.items():
        status = item.get("status") if isinstance(item, dict) else None
        if key in {"/healthz", "/readyz", "/login"} and status != 200:
            failures.append((key, status))
        if key.startswith("/api/") and status != 200:
            failures.append((key, status))
        if isinstance(item, dict) and item.get("has_bad_question_marks"):
            failures.append((key, "BAD_TEXT"))
    if failures:
        raise SystemExit(f"LIVE_HTTP_CHECK_FAILED {failures}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_historical_and_real_influenza_region_week_pipeline"


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    os.environ.setdefault("DASHBOARD_DATABASE_URL", os.environ.get("APP_DATABASE_URL") or os.environ.get("MIGRATION_DATABASE_URL", ""))
    salt = b"p1-16-dashboard-test-salt"
    password = b"unused-test-password"
    fake_auth = {
        "username": 'REDACTED',
        "salt_hex": salt.hex(),
        "hash_hex": hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1, dklen=32).hex(),
        "n": 16384,
        "r": 8,
        "p": 1,
    }
    tmp_auth = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json")
    tmp_auth.write(json.dumps(fake_auth, sort_keys=True))
    tmp_auth.close()
    os.environ["DASHBOARD_AUTH_FILE"] = tmp_auth.name
    from fastapi.testclient import TestClient
    from epidemic_core.dashboard.app import COOKIE_NAME, create_app

    app = create_app()
    with TestClient(app) as client:
        token = app.state.sessions.create()
        client.cookies.set(COOKIE_NAME, token, domain="testserver.local", path="/")
        root = client.get("/")
        api = client.get("/api/v1/prediction-demo")
        js = client.get("/static/dashboard.prediction-demo.js")
    try:
        payload = api.json()
    except Exception:
        payload = {}
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "root_status": root.status_code,
        "root_contains_prediction_panel": "prediction-demo-panel" in root.text,
        "root_mentions_real_flunet_or_real_data": ("REAL OFFICIAL DATA" in root.text) or ("FluNet" in root.text),
        "api_status": api.status_code,
        "api_status_value": payload.get("status"),
        "api_disease": payload.get("disease"),
        "api_source": payload.get("source"),
        "api_warning_contains_retrospective": "RETROSPECTIVE ENGINEERING DEMO" in json.dumps(payload, ensure_ascii=False),
        "api_warning_contains_not_ebola_validation": "DOES NOT VALIDATE EBOLA PREDICTION" in json.dumps(payload, ensure_ascii=False),
        "api_prediction_count": len(payload.get("predictions") or []),
        "api_metrics_present": bool(payload.get("metrics")),
        "static_js_status": js.status_code,
        "static_js_contains_fetch": "/api/v1/prediction-demo" in js.text,
        "root_sha256": sha_text(root.text),
        "api_sha256": sha_text(api.text),
        "passed": root.status_code == 200
        and "prediction-demo-panel" in root.text
        and api.status_code == 200
        and payload.get("disease") == "influenza"
        and payload.get("source") == "WHO FluNet"
        and len(payload.get("predictions") or []) > 0
        and "RETROSPECTIVE ENGINEERING DEMO" in json.dumps(payload, ensure_ascii=False)
        and js.status_code == 200,
        "database_writes": 0,
        "review_candidate_rows_used": 0,
    }
    try:
        Path(tmp_auth.name).unlink()
    except OSError:
        pass
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "dashboard_reconciliation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "P1_16_DASHBOARD_VERIFIED" if report["passed"] else "P1_16_DASHBOARD_VERIFY_FAILED", "passed": report["passed"], "api_prediction_count": report["api_prediction_count"]}, ensure_ascii=False, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

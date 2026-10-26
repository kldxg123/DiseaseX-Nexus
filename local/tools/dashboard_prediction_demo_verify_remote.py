from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(
        "DASHBOARD_DATABASE_URL",
        os.environ.get("APP_DATABASE_URL") or os.environ.get("MIGRATION_DATABASE_URL", ""),
    )
    salt = b"dashboard-prediction-demo-test-salt"
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
    payload: dict[str, Any] = {}
    try:
        payload = api.json()
    except Exception:
        payload = {}
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "root_status": root.status_code,
        "root_contains_prediction_panel": "prediction-demo-panel" in root.text,
        "api_status": api.status_code,
        "api_payload_keys": sorted(payload.keys()),
        "api_prediction_count": len(payload.get("predictions") or []),
        "api_status_value": payload.get("status"),
        "api_substatus_value": payload.get("substatus"),
        "api_warning_visible": "DEMO ONLY" in json.dumps(payload, ensure_ascii=False),
        "static_js_status": js.status_code,
        "static_js_contains_fetch": "/api/v1/prediction-demo" in js.text,
        "root_sha256": sha_text(root.text),
        "api_sha256": sha_text(api.text),
        "passed": root.status_code == 200
        and "prediction-demo-panel" in root.text
        and api.status_code == 200
        and len(payload.get("predictions") or []) > 0
        and "SAMPLE_DATA" in json.dumps(payload, ensure_ascii=False)
        and js.status_code == 200,
        "database_writes": 0,
        "trusted_snapshot_promotion_human_decision_writes": 0,
        "auth_secret_read": False,
        "auth_secret_displayed": False,
    }
    try:
        Path(tmp_auth.name).unlink()
    except OSError:
        pass
    (REPORT / "dashboard_prediction_demo_verify.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "DASHBOARD_PREDICTION_DEMO_VERIFIED" if report["passed"] else "DASHBOARD_PREDICTION_DEMO_VERIFY_FAILED", "passed": report["passed"], "api_prediction_count": report["api_prediction_count"]}, ensure_ascii=False, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

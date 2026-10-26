from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from epidemic_core.dashboard import app as dashboard


def _auth_file(tmp_path: Path) -> Path:
    password = 'REDACTED'
    salt = b"r4r2-test-salt-1234"
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    path = tmp_path / "dashboard_auth.json"
    path.write_text(
        json.dumps(
            {
                "username": 'REDACTED',
                "salt_hex": salt.hex(),
                "hash_hex": digest.hex(),
                "n": 16384,
                "r": 8,
                "p": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_kg_page_and_readonly_api_require_auth_then_render_after_test_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_FILE", str(_auth_file(tmp_path)))
    monkeypatch.setenv(
        "DASHBOARD_DATABASE_URL",
        "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432",
    )
    with TestClient(dashboard.create_app()) as client:
        unauth = client.get("/kg", follow_redirects=False)
        assert unauth.status_code in {303, 401}
        api_unauth = client.get("/api/v1/kg/filter-options")
        assert api_unauth.status_code == 401

        login = client.post(
            "/login",
            data={"username": 'REDACTED', "password": 'REDACTED'},
            follow_redirects=False,
        )
        assert login.status_code == 303

        page = client.get("/kg")
        assert page.status_code == 200
        html = page.text
        for marker in [
            "知识图谱：数据 → 推荐 → 论文溯源",
            "数据筛选与结果",
            "关系图与证据链",
            "模型推荐与推荐依据",
            "Paper 1 隔离",
        ]:
            assert marker in html

        options = client.get("/api/v1/kg/filter-options")
        assert options.status_code == 200
        assert {"countries", "diseases", "indicators", "frequencies"} <= set(options.json())

        datasets = client.get("/api/v1/kg/datasets", params={"country": "BRA", "disease": "dengue", "limit": 5})
        assert datasets.status_code == 200
        assert "items" in datasets.json()

        guidance = client.get(
            "/api/v1/kg/recommendations",
            params={"country": "BRA", "disease": "dengue", "indicator": "cases_new", "frequency": "week", "horizon": 4},
        )
        assert guidance.status_code in {200, 422, 503}
        if guidance.status_code == 200:
            body = guidance.json()
            assert "kg_explorer_adapter" in body
            assert "compatible_models" in body or "candidate_models" in body or "claim_matches" in body

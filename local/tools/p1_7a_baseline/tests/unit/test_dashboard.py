from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from epidemic_core.dashboard import app as dashboard


class FakeEngine:
    def dispose(self) -> None:
        pass


def auth_file(tmp_path: Path, password: str = "correct horse battery staple") -> Path:
    salt = b"0123456789abcdef"
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    path = tmp_path / "login_hash.json"
    path.write_text(json.dumps({"username": 'REDACTED', "salt_hex": salt.hex(), "hash_hex": digest.hex()}))
    return path


def fake_query(_engine: Any, sql: str, _params: Any = None) -> list[dict[str, Any]]:
    catalog = {
        "snapshot_id": "11111111-1111-4111-8111-111111111111",
        "country_alpha2": "FR",
        "country_name": "France",
        "disease_slug": "covid-19",
        "source_key": "who_covid19_global_weekly",
        "requested_start": "2024-01-01",
        "requested_end": "2024-03-31",
        "snapshot_created_at": "2026-01-01T00:00:00+00:00",
        "snapshot_schema_version": 1,
        "observation_count": 2,
        "snapshot_sha256": "a" * 64,
        "artifact_sha12": "b" * 12,
        "contract_version": "who-covid19-normalization-v2",
        "attribution": "World Health Organization",
        "terms_url": "https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions",
        "warning_count": 1,
        "granularities": ["week"],
    }
    if "SELECT 1 AS ready" in sql:
        return [{"ready": 1}]
    if "DISTINCT country_alpha2" in sql:
        return [{"country_alpha2": "FR", "country_name": "France"}]
    if "DISTINCT disease_slug" in sql:
        return [{"disease_slug": "covid-19"}]
    if "count(*) AS total FROM epidemic.dashboard_snapshot_catalog" in sql:
        return [{"total": 1}]
    if "dashboard_snapshot_catalog_v1 WHERE snapshot_id" in sql:
        return [catalog]
    if "SELECT * FROM epidemic.dashboard_snapshot_catalog" in sql:
        return [catalog]
    if "count(*) AS total FROM epidemic.dashboard_observation" in sql:
        return [{"total": 2}]
    if "FROM epidemic.dashboard_observation_series_v1" in sql:
        return [
            {
                "period_start": "2024-01-01",
                "period_end": "2024-01-07",
                "granularity": "week",
                "metric_code": "cases_new",
                "value_numeric": "0",
                "unit": "count",
                "revision": 1,
                "quality_severity": "pass",
                "quality_flags": [],
                "default_train_eligible": True,
                "source_date_type": None,
            },
            {
                "period_start": "2024-01-01",
                "period_end": "2024-01-07",
                "granularity": "week",
                "metric_code": "deaths_new",
                "value_numeric": "-1",
                "unit": "count",
                "revision": 2,
                "quality_severity": "warning",
                "quality_flags": ["NEGATIVE_REPORTED_REVISION"],
                "default_train_eligible": False,
                "source_date_type": None,
            },
        ]
    if "FROM epidemic.dashboard_quality_summary_v1" in sql:
        return [
            {
                "period_start": "2024-01-01",
                "period_end": "2024-01-07",
                "granularity": "week",
                "pass_count": 1,
                "warning_count": 1,
                "fail_count": 0,
                "default_train_eligible_count": 1,
                "missing_expected_metric_count": 0,
                "expected_metric_status": "known",
                "quality_flags": [["NEGATIVE_REPORTED_REVISION"]],
            }
        ]
    raise AssertionError(sql)


def client(tmp_path: Path, monkeypatch: Any) -> TestClient:
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", "postgresql+psycopg://placeholder")
    monkeypatch.setenv("DASHBOARD_AUTH_FILE", str(auth_file(tmp_path)))
    monkeypatch.setattr(dashboard, "create_engine", lambda *_a, **_k: FakeEngine())
    monkeypatch.setattr(dashboard, "_query", fake_query)
    return TestClient(dashboard.create_app())


def login(c: TestClient, password: str = "correct horse battery staple") -> Any:
    return c.post("/login", data={"username": 'REDACTED', "password": password}, follow_redirects=False)


def test_auth_cookie_headers_logout_and_restart(tmp_path: Path, monkeypatch: Any) -> None:
    with client(tmp_path, monkeypatch) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/readyz").status_code == 401
        response = login(c)
        assert response.status_code == 303
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
        assert c.get("/readyz").status_code == 200
        page = c.get("/")
        assert page.status_code == 200
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
        assert "access-control-allow-origin" not in page.headers
        assert c.post("/logout", follow_redirects=False).status_code == 303
        assert c.get("/api/v1/options").status_code == 401
    with client(tmp_path, monkeypatch) as restarted:
        restarted.cookies.set(dashboard.COOKIE_NAME, "old-session")
        assert restarted.get("/api/v1/options").status_code == 401


def test_fifth_failure_locks_and_error_is_uniform(tmp_path: Path, monkeypatch: Any) -> None:
    with client(tmp_path, monkeypatch) as c:
        statuses = [login(c, "wrong").status_code for _ in range(5)]
        assert statuses == [401, 401, 401, 401, 429]
        assert login(c).status_code == 429


def test_series_validation_and_real_semantics(tmp_path: Path, monkeypatch: Any) -> None:
    with client(tmp_path, monkeypatch) as c:
        assert login(c).status_code == 303
        assert c.get("/api/v1/snapshots/not-a-uuid/series").status_code == 400
        sid = "11111111-1111-4111-8111-111111111111"
        assert c.get(f"/api/v1/snapshots/{sid}/series?start_date=2024-1-1").status_code == 400
        value = c.get(f"/api/v1/snapshots/{sid}/series?start_date=2024-01-01&end_date=2024-01-31").json()
        assert value["point_count"] == 2
        assert [x["value_numeric"] for x in value["points"]] == ["0", "-1"]
        assert value["interpolated"] is False and value["resampled"] is False and value["predicted"] is False


def test_html_svg_accessibility_and_no_unsafe_rendering() -> None:
    html = dashboard.DASHBOARD_HTML.format(nonce="test")
    assert html.count('role="img"') == 3
    assert html.count('aria-label="') >= 4
    assert "innerHTML" not in html
    assert "textContent" in html
    assert "serviceWorker" not in html and "cdn" not in html.lower()
    assert "不会重新发起网络抓取" in html
    assert "尚未执行疫情预测或爆发时间计算" in html
    assert "该数据源未提供这一时期的死亡指标，不等于死亡数为 0。" in html
    assert '$("death-empty").hidden=values.length>0' in html
    assert "const groups=new Map()" in html
    assert "value:p?Number(p.value_numeric):null" in html

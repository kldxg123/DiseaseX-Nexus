from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from epidemic_core.daily.service import DailyUpdateError, load_config, next_due

ROOT = Path(__file__).parents[2]


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "daily.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "daily-update-config-v1",
                "job_name": "epidemic-daily-update-trigger",
                "cron_expression": "30 2 * * *",
                "timezone": "Asia/Shanghai",
                "local_execution_time": "02:30:00",
                "catch_up_policy": "coalesce_latest",
                "revision_lookback": {"approved": {"unit": "week", "count": 12}},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_daily_config_and_next_due_are_explicit(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    assert config["cron_expression"] == "30 2 * * *"
    before = dt.datetime(2026, 7, 13, 18, 29, tzinfo=dt.timezone.utc)
    after = dt.datetime(2026, 7, 13, 18, 31, tzinfo=dt.timezone.utc)
    assert next_due(before, "Asia/Shanghai", dt.time(2, 30)) == dt.datetime(2026, 7, 13, 18, 30, tzinfo=dt.timezone.utc)
    assert next_due(after, "Asia/Shanghai", dt.time(2, 30)) == dt.datetime(2026, 7, 14, 18, 30, tzinfo=dt.timezone.utc)


def test_daily_config_rejects_scheduler_drift(tmp_path: Path) -> None:
    path = _config(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["cron_expression"] = "*/5 * * * *"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DailyUpdateError, match="DAILY_CONFIG_INVALID"):
        load_config(path)


def test_openclaw_cron_is_command_only_exact_and_non_delivering() -> None:
    script = (ROOT / "scripts/daily_update/install_openclaw_cron.sh").read_text(encoding="utf-8")
    for required in (
        "CRON='30 2 * * *'",
        "TIMEZONE='Asia/Shanghai'",
        '--cron "$CRON"',
        '--tz "$TIMEZONE"',
        "--exact",
        "--command-argv",
        "--timeout-seconds 120",
        "--no-deliver",
    ):
        assert required in script
    assert "--model" not in script
    assert "sessionTarget" not in script
    assert "BRAVE_SEARCH_API_KEY" not in script


def test_only_openclaw_cron_and_startup_reconcile_are_wired() -> None:
    start_all = (ROOT / "scripts/services/start_all.sh").read_text(encoding="utf-8")
    assert "install_openclaw_cron.sh" in start_all
    assert 'reconcile_due.sh" --trigger startup' in start_all
    assert start_all.count("start_daily_update.sh") == 1
    daily_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "scripts/daily_update").glob("*.sh"))
    for forbidden in ("pg_cron", "systemd timer", "crontab -", "APScheduler", "Celery", "RQ"):
        assert forbidden not in daily_text


def test_daily_dashboard_asset_is_read_only_and_xss_safe() -> None:
    script = (ROOT / "src/epidemic_core/dashboard/static/dashboard.p1-8-daily.js").read_text(encoding="utf-8")
    page = (ROOT / "src/epidemic_core/dashboard/app.py").read_text(encoding="utf-8")
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "localStorage" not in script and "sessionStorage" not in script
    assert "window.setInterval(load,60000)" in script
    assert '"/api/v1/daily-updates"' in page
    assert "server_shutdown_limitation" in page


def test_daily_api_and_tools_accept_no_url_or_code() -> None:
    route = (ROOT / "src/epidemic_core/api/routes/daily_updates.py").read_text(encoding="utf-8")
    plugin = (ROOT / "integrations/openclaw/epidemic-query-tools/src/index.ts").read_text(encoding="utf-8")
    assert "schedule_id: uuid.UUID" in route
    assert "DailyStart=Type.Object({schedule_id:" in plugin
    daily_contract = plugin.split("const DailyStart=", 1)[1].split("const DailyStatus=", 1)[0]
    for forbidden in ("url", "sql", "shell", "command", "provider", "code"):
        assert forbidden not in daily_contract.lower()


def test_unchanged_source_does_not_recount_historical_normalization() -> None:
    worker = (ROOT / "src/epidemic_core/daily/worker.py").read_text(encoding="utf-8")
    block = worker.split("if not raw_stored:", 1)[1].split("snapshot_new =", 1)[0]
    assert "added = 0" in block
    assert "revised = 0" in block
    assert "excluded = 0" in block


def test_review_role_receives_only_schema_usage_and_status_view_select() -> None:
    migration = (ROOT / "migrations/versions/0010_p1_8_daily_updates.py").read_text(encoding="utf-8")
    assert "GRANT USAGE ON SCHEMA epidemic TO epidemicreview" in migration
    assert "GRANT SELECT ON epidemic.daily_update_status_v1 TO epidemicreview" in migration

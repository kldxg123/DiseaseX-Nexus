from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()


def db_url() -> str:
    raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("APP_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL_UNAVAILABLE")
    url = make_url(raw)
    password = os.environ.get("POSTGRES_ADMIN_PASSWORD")
    if password:
        url = url.set(username=os.environ.get("POSTGRES_ADMIN_USER") or "epidemicdb", password=password)
    return url.render_as_string(hide_password=False)


def rows(conn: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def cmd(args: list[str], timeout: int = 60) -> dict[str, Any]:
    p = subprocess.run(args, cwd=PROJECT, text=True, capture_output=True, timeout=timeout)
    return {"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def main() -> None:
    engine = create_engine(db_url(), future=True, pool_pre_ping=True)
    out: dict[str, Any] = {
        "time_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project": str(PROJECT),
        "git_head": cmd(["git", "rev-parse", "HEAD"])["stdout"].strip(),
        "git_branch": cmd(["git", "branch", "--show-current"])["stdout"].strip(),
        "git_status_v2_z_sha256": None,
    }
    status = subprocess.run(["git", "status", "--porcelain=v2", "-z"], cwd=PROJECT, capture_output=True)
    import hashlib

    out["git_status_v2_z_sha256"] = hashlib.sha256(status.stdout).hexdigest()
    out["git_dirty_lines"] = len([x for x in status.stdout.split(b"\0") if x])
    with engine.connect() as conn:
        out["alembic"] = rows(
            conn,
            "SELECT table_schema, table_name FROM information_schema.tables WHERE table_name='alembic_version' ORDER BY table_schema",
        )
        out["protected_runs"] = rows(
            conn,
            """
            SELECT id::text,status,run_key,disease_slug,target_metric,country_iso3,source_key,cutoff_week,dataset_sha256,
                   champion_model,published_at,retracted_at,retraction_reason
            FROM epidemic.forecast_runs
            WHERE id::text IN ('c1ce1947-1ab4-5f2d-b69e-a4e9c4668825','2adad614-7f25-5e58-ba00-167ca5f87606')
            ORDER BY id::text
            """,
        )
        out["disease_counts"] = rows(
            conn,
            """
            SELECT d.slug, count(*)::int rows, min(o.period_start)::text date_min, max(o.period_end)::text date_max
            FROM epidemic.normalized_observations o JOIN epidemic.diseases d ON d.id=o.disease_id
            WHERE d.slug IN ('legionella','hantavirus','nipah')
            GROUP BY d.slug ORDER BY d.slug
            """,
        )
        out["nipah_current"] = rows(
            conn,
            """
            SELECT o.id::text,o.supersedes_observation_id::text AS supersedes,o.country_alpha2,l.country_iso3,
                   o.metric_code,o.value_numeric::int AS value,o.period_start::text,o.granularity,
                   o.dimensions->>'don_id' AS don_id,o.dimensions->>'title' AS title,
                   l.source_url,l.artifact_sha256,l.source_row_locator
            FROM epidemic.normalized_observations o
            JOIN epidemic.diseases d ON d.id=o.disease_id
            LEFT JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
            WHERE d.slug='nipah'
              AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
            ORDER BY don_id,metric_code,country_iso3,o.id
            """,
        )
        out["dashboard_catalog"] = rows(
            conn,
            """
            SELECT disease_slug,source_key,country_alpha3,observation_count::int,data_start::text,data_end::text
            FROM epidemic.dashboard_history_catalog_v2
            WHERE disease_slug IN ('legionella','hantavirus','nipah')
            ORDER BY disease_slug,source_key,country_alpha3
            """,
        )
        out["dashboard_series_counts"] = rows(
            conn,
            """
            SELECT disease_slug,source_key,country_alpha3,metric_code,granularity,count(*)::int rows,
                   min(period_start)::text date_min,max(period_end)::text date_max,
                   sum(CASE WHEN value_numeric=0 THEN 1 ELSE 0 END)::int explicit_zero,
                   sum(CASE WHEN value_numeric>0 THEN 1 ELSE 0 END)::int nonzero
            FROM epidemic.dashboard_history_series_v2
            WHERE disease_slug IN ('legionella','hantavirus','nipah')
            GROUP BY disease_slug,source_key,country_alpha3,metric_code,granularity
            ORDER BY disease_slug,source_key,country_alpha3,metric_code
            """,
        )
        out["forecast_runs_three_disease"] = rows(
            conn,
            """
            SELECT id::text,status,run_key,disease_slug,target_metric,country_iso3,source_key,cutoff_week,
                   dataset_sha256,champion_model,validation_level,published_at
            FROM epidemic.forecast_runs
            WHERE disease_slug IN ('legionella','hantavirus','nipah')
            ORDER BY created_at DESC
            """,
        )
    # route inventory from source only; no secrets
    source = (PROJECT / "src" / "epidemic_core" / "dashboard" / "app.py")
    route_lines = []
    if source.exists():
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"@app\.(get|post|put|delete)\(\"([^\"]+)\"", line)
            if m:
                route_lines.append({"method": m.group(1).upper(), "path": m.group(2)})
    out["dashboard_source_routes"] = route_lines
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()

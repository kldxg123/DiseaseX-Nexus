from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/p1_15_ebola_cod_trusted_expansion"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_obj(value: Any) -> str:
    return sha_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault).encode("utf-8"))


def write_json(name: str, value: Any) -> None:
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def cmd(args: list[str], timeout: int = 60) -> dict[str, Any]:
    cp = subprocess.run(args, cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    return {"args": args, "returncode": cp.returncode, "stdout": cp.stdout, "stderr_sha256": sha_bytes(cp.stderr.encode("utf-8"))}


def sql_log_entry(label: str, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"label": label, "sql_sha256": sha_bytes(statement.encode("utf-8")), "params_keys": sorted((params or {}).keys())}


SQL_LOG: list[dict[str, Any]] = []


def run_sql(conn, label: str, statement: str, params: dict[str, Any] | None = None):
    SQL_LOG.append(sql_log_entry(label, statement, params))
    return conn.execute(text(statement), params or {})


def table_exists(conn, schema: str, table: str) -> bool:
    return bool(run_sql(conn, f"exists_{schema}_{table}", "SELECT to_regclass(:name) IS NOT NULL", {"name": f"{schema}.{table}"}).scalar_one())


def columns(conn, schema: str, table: str) -> list[str]:
    return [
        str(r["column_name"])
        for r in run_sql(
            conn,
            f"columns_{schema}_{table}",
            "SELECT column_name FROM information_schema.columns WHERE table_schema=:schema AND table_name=:table ORDER BY ordinal_position",
            {"schema": schema, "table": table},
        )
        .mappings()
        .all()
    ]


def rowset_digest(conn, label: str, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = [r["j"] for r in run_sql(conn, label, sql, params).mappings().all()]
    normalized = []
    for row in rows:
        if isinstance(row, str):
            try:
                row = json.loads(row)
            except Exception:
                pass
        normalized.append(row)
    return {"row_count": len(normalized), "row_set_sha256": sha_obj(sorted(normalized, key=lambda x: json.dumps(x, sort_keys=True, default=jdefault)))}


def maybe_count(conn, schema: str, table: str, where: str = "", params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not table_exists(conn, schema, table):
        return {"exists": False, "row_count": None}
    value = int(run_sql(conn, f"count_{schema}_{table}_{sha_bytes(where.encode())[:8]}", f"SELECT count(*)::bigint FROM \"{schema}\".\"{table}\" {where}", params).scalar_one())
    return {"exists": True, "row_count": value}


def git_and_docs() -> dict[str, Any]:
    status = cmd(["git", "status", "--porcelain=v2", "-uall"])
    branch = cmd(["git", "branch", "--show-current"])
    head = cmd(["git", "rev-parse", "HEAD"])
    staged = cmd(["git", "diff", "--cached", "--name-only"])
    docs = []
    for pattern in ["AGENTS.md", "README*", "docs/**/*", "contracts/**/*", "reference/**/*", "alembic.ini", "migrations/versions/*.py"]:
        for p in sorted(PROJECT.glob(pattern)):
            if p.is_file() and ".git" not in p.parts and p.stat().st_size <= 2_000_000:
                docs.append({"path": str(p.relative_to(PROJECT)), "bytes": p.stat().st_size, "sha256": sha_bytes(p.read_bytes())})
    return {
        "branch": branch["stdout"].strip(),
        "head": head["stdout"].strip(),
        "staged_paths": [x for x in staged["stdout"].splitlines() if x],
        "dirty_paths": [line for line in status["stdout"].splitlines() if line],
        "dirty_count": len([line for line in status["stdout"].splitlines() if line]),
        "docs_inventory": docs,
        "docs_inventory_sha256": sha_obj(docs),
    }


def runtime() -> dict[str, Any]:
    ps = cmd(["bash", "-lc", "ps -eo pid,lstart,cmd | grep -E 'uvicorn|dashboard|source_review|daily_update|preview_publication|epidemic' | grep -v grep || true"])
    ss = cmd(["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(8020|8021|5432|8000|8001)\\b' || true"])
    scripts = {}
    for name, rel in {
        "daily": "scripts/daily_update/status_daily_update.sh",
        "preview": "scripts/preview_publication_worker/status_preview_publication_worker.sh",
        "review": "scripts/source_review/status_source_review.sh",
        "dashboard": "scripts/dashboard/status_dashboard.sh",
    }.items():
        path = PROJECT / rel
        if path.exists():
            cp = subprocess.run([str(path)], cwd=PROJECT, text=True, capture_output=True)
            scripts[name] = {"exists": True, "returncode": cp.returncode, "stdout_sha256": sha_bytes(cp.stdout.encode()), "stderr_sha256": sha_bytes(cp.stderr.encode())}
        else:
            scripts[name] = {"exists": False}
    return {"processes_redacted": ps["stdout"], "listeners": ss["stdout"], "service_scripts": scripts, "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}


def database() -> dict[str, Any]:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    engine = sa.create_engine(url, hide_parameters=True, future=True)
    try:
        with engine.connect() as conn:
            run_sql(conn, "begin", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            run_sql(conn, "timeout", "SET LOCAL statement_timeout='30s'")
            identity = dict(run_sql(conn, "identity", "SELECT current_database() AS current_database,current_user,session_user,current_setting('transaction_read_only') AS transaction_read_only,current_setting('transaction_isolation') AS transaction_isolation").mappings().one())
            revisions = [r["version_num"] for r in run_sql(conn, "revisions", "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num").mappings().all()]
            catalog_tables = [
                dict(r)
                for r in run_sql(conn, "tables", "SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema LIKE 'epidemic%' ORDER BY table_schema,table_name").mappings().all()
            ]
            countries = [dict(r) for r in run_sql(conn, "cod_country", "SELECT to_jsonb(t) AS j FROM epidemic.countries t WHERE to_jsonb(t)::text ILIKE '%COD%' OR to_jsonb(t)::text ILIKE '%Congo%' ORDER BY to_jsonb(t)::text").mappings().all()]
            diseases = [dict(r) for r in run_sql(conn, "ebola_disease", "SELECT to_jsonb(t) AS j FROM epidemic.diseases t WHERE to_jsonb(t)::text ILIKE '%ebola%' ORDER BY to_jsonb(t)::text").mappings().all()]
            trusted_series = rowset_digest(conn, "trusted_cod_ebola_series", "SELECT j FROM (SELECT to_jsonb(t) AS j FROM epidemic_p1_12.trusted_dashboard_series_v1 t) x WHERE (j->>'country_alpha2'='CD' OR j->>'country_alpha3'='COD') AND lower(j::text) LIKE '%ebola%' ORDER BY j::text") if table_exists(conn, "epidemic_p1_12", "trusted_dashboard_series_v1") else {"row_count": None}
            trusted_catalog = rowset_digest(conn, "trusted_cod_ebola_catalog", "SELECT j FROM (SELECT to_jsonb(t) AS j FROM epidemic_p1_12.trusted_dashboard_catalog_v1 t) x WHERE (j->>'country_alpha2'='CD' OR j->>'country_alpha3'='COD') AND lower(j::text) LIKE '%ebola%' ORDER BY j::text") if table_exists(conn, "epidemic_p1_12", "trusted_dashboard_catalog_v1") else {"row_count": None}
            norm_cols = columns(conn, "epidemic", "normalized_observations") if table_exists(conn, "epidemic", "normalized_observations") else []
            counts = {
                "data_sources": maybe_count(conn, "epidemic", "data_sources"),
                "normalized_observations": maybe_count(conn, "epidemic", "normalized_observations"),
                "dataset_snapshots": maybe_count(conn, "epidemic", "dataset_snapshots"),
                "preview_review_ready_v2": maybe_count(conn, "epidemic_p1_12", "review_ready_candidates_v2"),
                "source_review_cases": maybe_count(conn, "epidemic_discovery", "source_review_cases"),
                "p1_14c_preview_rows_v2": maybe_count(conn, "epidemic_p1_12", "candidate_preview_rows_v2"),
            }
            distributions = {}
            if table_exists(conn, "epidemic_p1_12", "trusted_dashboard_series_v1"):
                base = "(SELECT to_jsonb(t) AS j FROM epidemic_p1_12.trusted_dashboard_series_v1 t) x"
                filt = "(j->>'country_alpha2'='CD' OR j->>'country_alpha3'='COD') AND lower(j::text) LIKE '%ebola%'"
                distributions["cod_ebola_metric"] = [dict(r) for r in run_sql(conn, "dist_metric", f"SELECT coalesce(j->>'metric_slug',j->>'metric') AS metric,count(*)::bigint AS rows FROM {base} WHERE {filt} GROUP BY 1 ORDER BY 1").mappings().all()]
                distributions["cod_ebola_classification"] = [dict(r) for r in run_sql(conn, "dist_class", f"SELECT coalesce(j->>'classification',j->>'case_classification') AS classification,count(*)::bigint AS rows FROM {base} WHERE {filt} GROUP BY 1 ORDER BY 1").mappings().all()]
                distributions["cod_ebola_granularity"] = [dict(r) for r in run_sql(conn, "dist_gran", f"SELECT j->>'temporal_granularity' AS temporal_granularity,count(*)::bigint AS rows FROM {base} WHERE {filt} GROUP BY 1 ORDER BY 1").mappings().all()]
                distributions["cod_ebola_dates"] = dict(run_sql(conn, "date_range", f"SELECT min(j->>'data_as_of') AS earliest_data_as_of,max(j->>'data_as_of') AS latest_data_as_of,count(distinct coalesce(j->>'outbreak_event_id',j->>'event_id'))::bigint AS outbreak_events FROM {base} WHERE {filt}").mappings().one())
            other_digest = rowset_digest(conn, "other_country_disease_digest", "SELECT to_jsonb(x) AS j FROM (SELECT coalesce(j->>'country_alpha3',j->>'country_alpha2') AS country_code,coalesce(j->>'disease_slug',j->>'disease') AS disease,count(*)::bigint AS rows FROM (SELECT to_jsonb(t) AS j FROM epidemic_p1_12.trusted_dashboard_series_v1 t) s WHERE NOT ((j->>'country_alpha2'='CD' OR j->>'country_alpha3'='COD') AND lower(j::text) LIKE '%ebola%') GROUP BY 1,2 ORDER BY 1,2) x") if table_exists(conn, "epidemic_p1_12", "trusted_dashboard_series_v1") else {"row_count": None}
            run_sql(conn, "rollback", "ROLLBACK")
    finally:
        engine.dispose()
    result = {
        "identity": identity,
        "alembic_revisions": revisions,
        "catalog_table_count": len(catalog_tables),
        "catalog_tables_sha256": sha_obj(catalog_tables),
        "countries_candidates": countries,
        "ebola_disease_candidates": diseases,
        "normalized_observations_columns": norm_cols,
        "counts": counts,
        "cod_ebola_trusted_series": trusted_series,
        "cod_ebola_trusted_catalog": trusted_catalog,
        "distributions": distributions,
        "other_country_disease_digest": other_digest,
        "sql_log": SQL_LOG,
    }
    result["sha256"] = sha_obj({k: v for k, v in result.items() if k != "sql_log"})
    return result


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    base = {"git": git_and_docs(), "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "p1_14_chain_paused": True, "p1_14f_entered": False}
    rt = runtime()
    db = database()
    write_json("baseline.json", base)
    write_json("runtime_before.json", rt)
    write_json("database_before.json", db)
    print(json.dumps({"status": "BASELINE_CAPTURED", "head": base["git"]["head"], "dirty_count": base["git"]["dirty_count"], "db_revision": db["alembic_revisions"], "cod_ebola_rows": db["cod_ebola_trusted_series"].get("row_count")}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

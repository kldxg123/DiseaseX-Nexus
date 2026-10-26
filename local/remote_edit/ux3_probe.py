from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("USER_HOME/projects/openclaw")
ENV = ROOT / "runtime" / "secrets" / "vm201_runtime.env"


def load_env() -> None:
    if not ENV.exists():
        return
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if not k:
            continue
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def sha_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def http_get(path: str) -> dict:
    url = f"http://127.0.0.1:8020{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ux3-probe/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read(1024 * 1024)
            return {
                "path": path,
                "status": resp.status,
                "content_type": resp.headers.get("content-type"),
                "bytes_sampled": len(data),
                "sha256_sample": hashlib.sha256(data).hexdigest(),
                "has_html": b"<html" in data[:4096].lower() or b"<!doctype" in data[:4096].lower(),
                "has_raw_json_prefix": data.lstrip()[:1] in (b"{", b"["),
            }
    except Exception as e:
        return {"path": path, "error_type": type(e).__name__, "error": str(e)[:300]}


def db_probe() -> dict:
    import psycopg

    dsn = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("EPIDEMIC_DATABASE_URL")
        or os.environ.get("PG_DSN")
    )
    if not dsn and (ROOT / "runtime" / "postgres-run").exists():
        dsn = "postgresql://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"
    if not dsn:
        return {"error": "NO_DSN_ENV"}
    out: dict = {}
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute("SHOW transaction_read_only")
            out["transaction_read_only"] = cur.fetchone()[0]
            cur.execute("select current_database(), current_user, inet_server_port()")
            db, user, port = cur.fetchone()
            out["identity"] = {"database": db, "role": user, "port": port}
            cur.execute(
                """
                select table_schema, table_name
                from information_schema.tables
                where table_name='alembic_version'
                order by table_schema
                """
            )
            av = cur.fetchall()
            out["alembic_tables"] = av
            out["alembic_versions"] = []
            for schema, _table in av:
                try:
                    cur.execute(f"select version_num from {schema}.alembic_version order by version_num")
                    out["alembic_versions"].extend([r[0] for r in cur.fetchall()])
                except Exception as exc:
                    out.setdefault("alembic_errors", []).append(type(exc).__name__)
                    conn.rollback()
                    cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    cur.execute("SET LOCAL statement_timeout = '30s'")
            tables = [
                ("epidemic_kg", "kg_nodes"),
                ("epidemic_kg", "kg_edges"),
                ("epidemic_kg", "kg_claims"),
                ("epidemic_kg", "kg_evidence_spans"),
                ("epidemic_kg", "kg_forecast_shadow_runs"),
                ("epidemic_kg", "kg_forecast_shadow_values"),
                ("epidemic", "training_observations_v1"),
            ]
            counts = {}
            cols = {}
            for schema, table in tables:
                cur.execute(
                    """
                    select count(*) from information_schema.tables
                    where table_schema=%s and table_name=%s
                    """,
                    (schema, table),
                )
                if cur.fetchone()[0] == 0:
                    counts[f"{schema}.{table}"] = None
                    continue
                cur.execute(f"select count(*) from {schema}.{table}")
                counts[f"{schema}.{table}"] = cur.fetchone()[0]
                cur.execute(
                    """
                    select column_name, data_type
                    from information_schema.columns
                    where table_schema=%s and table_name=%s
                    order by ordinal_position
                    """,
                    (schema, table),
                )
                cols[f"{schema}.{table}"] = cur.fetchall()
            out["counts"] = counts
            out["columns"] = cols
            # Protected run checks.
            cur.execute(
                """
                select table_schema, table_name, column_name
                from information_schema.columns
                where column_name in ('run_id','status','dataset_sha256','invalidated_reason','reason_code')
                order by table_schema, table_name, column_name
                """
            )
            out["run_related_columns"] = cur.fetchall()
            # Candidate training series summary, excluding CDC-like sources when possible.
            try:
                colnames = [c[0] for c in cols.get("epidemic.training_observations_v1", [])]
                country_col = next((c for c in ["country_iso3", "location_code", "country", "country_id"] if c in colnames), None)
                disease_col = next((c for c in ["canonical_disease", "disease", "disease_id"] if c in colnames), None)
                cadence_col = next((c for c in ["cadence", "period_cadence", "time_scale"] if c in colnames), None)
                metric_col = next((c for c in ["metric", "metric_name", "target_metric"] if c in colnames), None)
                week_col = next((c for c in ["period_start", "week_start", "week", "target_week"] if c in colnames), None)
                value_col = next((c for c in ["value", "actual_value", "y", "observed_value"] if c in colnames), None)
                out["training_column_mapping"] = {
                    "country": country_col,
                    "disease": disease_col,
                    "cadence": cadence_col,
                    "metric": metric_col,
                    "period": week_col,
                    "value": value_col,
                }
                if all([country_col, disease_col, metric_col, week_col, value_col]):
                    cadence_expr = cadence_col or "'week'"
                    cur.execute(
                        f"""
                        select {country_col}::text, {disease_col}::text, {cadence_expr}::text, {metric_col}::text,
                               count(*) as n, count({value_col}) as nonnull_n,
                               min({week_col}), max({week_col}),
                               md5(string_agg(coalesce({country_col}::text,'')||'|'||coalesce({disease_col}::text,'')||'|'||coalesce({metric_col}::text,'')||'|'||{week_col}::text||'|'||coalesce({value_col}::text,''), ',' order by {week_col})) as series_md5
                        from epidemic.training_observations_v1
                        where {value_col} is not null
                        group by 1,2,3,4
                        order by nonnull_n desc, max({week_col}) desc
                        limit 20
                        """
                    )
                    out["top_training_series"] = [
                        {
                            "country_iso3": r[0],
                            "disease": r[1],
                            "cadence": r[2],
                            "metric": r[3],
                            "rows": r[4],
                            "nonnull_rows": r[5],
                            "min_period_start": str(r[6]),
                            "max_period_start": str(r[7]),
                            "series_md5": r[8],
                        }
                        for r in cur.fetchall()
                    ]
            except Exception as exc:
                out["top_training_series_error"] = {"type": type(exc).__name__, "message": str(exc)[:240]}
                conn.rollback()
                cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute("ROLLBACK")
    return out


def main() -> int:
    load_env()
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "runtime" / "python-packages"))
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_project_root": str(ROOT),
        "hostname": socket.gethostname(),
        "source_files": {
            "dashboard_app": sha_file(ROOT / "src/epidemic_core/dashboard/app.py"),
            "kg_p1_dashboard": sha_file(ROOT / "src/epidemic_core/kg_p1/dashboard_app.py"),
            "forecast_kg_dashboard": sha_file(ROOT / "src/epidemic_core/forecast_model_kg/dashboard_app.py"),
            "r4_shadow": sha_file(ROOT / "src/epidemic_core/forecast_model_kg/r4_shadow.py"),
            "forecast_js": sha_file(ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js"),
            "forecast_css": sha_file(ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.css"),
            "static_graph": sha_file(ROOT / "reference/forecast_model_knowledge_graph.v1.json"),
        },
        "routes": [http_get(p) for p in ["/healthz", "/readyz", "/forecast-model-knowledge-graph", "/kg/explorer", "/fasttrack-forecast", "/predictions"]],
    }
    try:
        result["database"] = db_probe()
    except Exception as e:
        result["database"] = {"error_type": type(e).__name__, "error": str(e)[:500]}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

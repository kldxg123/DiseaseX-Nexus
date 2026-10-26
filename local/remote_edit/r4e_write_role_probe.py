from __future__ import annotations

import hashlib
import json
from pathlib import Path

import psycopg


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip("'").strip('"')
    return env


def test_dsn(alias: str, user: str, password: str) -> dict[str, object]:
    dsn = f"postgresql://{user}:{password}@127.0.0.1:55432/epidemic_openclaw"
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("BEGIN")
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                """
                select current_user,
                       has_table_privilege(current_user,'epidemic_p1_12.trusted_dashboard_catalog_v1','INSERT'),
                       has_table_privilege(current_user,'epidemic_p1_12.trusted_dashboard_series_v1','INSERT'),
                       has_table_privilege(current_user,'epidemic.forecast_runs','INSERT')
                """
            ).fetchone()
            conn.execute("ROLLBACK")
            return {
                "alias": alias,
                "connect": True,
                "user_sha256_prefix": hashlib.sha256(str(row[0]).encode()).hexdigest()[:12],
                "catalog_insert": bool(row[1]),
                "series_insert": bool(row[2]),
                "forecast_insert": bool(row[3]),
            }
    except Exception as exc:
        return {"alias": alias, "connect": False, "error_class": exc.__class__.__name__}


def main() -> None:
    env = load_env(Path("USER_HOME/projects/openclaw/runtime/secrets/vm201_runtime.env"))
    candidates = []
    if "EPIDEMIC_ROLE_PASS" in env:
        for user in ["epidemic_app", "epidemic_owner", "epidemic_writer", "epidemic"]:
            candidates.append((f"epidemic_role_pass/{user}", user, env["EPIDEMIC_ROLE_PASS"]))
    if "PG_SUPERPASS" in env:
        for user in ["postgres", "epidemic_admin"]:
            candidates.append((f"pg_superpass/{user}", user, env["PG_SUPERPASS"]))
    print(json.dumps([test_dsn(*c) for c in candidates], indent=2))


if __name__ == "__main__":
    main()

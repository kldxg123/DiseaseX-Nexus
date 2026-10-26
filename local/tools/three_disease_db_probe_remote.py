from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


def candidate_urls() -> list[tuple[str, URL]]:
    out: list[tuple[str, URL]] = []
    raw_names = ["MIGRATION_DATABASE_URL", "APP_DATABASE_URL", "DATABASE_URL"]
    for name in raw_names:
        raw = os.environ.get(name)
        if raw:
            try:
                out.append((name + ":as_is", make_url(raw)))
            except Exception:
                pass
    base_raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("APP_DATABASE_URL")
    if base_raw:
        base = make_url(base_raw)
        for user_var, pass_var, label in [
            ("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD", "admin_env"),
            ("POSTGRES_USER", "POSTGRES_PASSWORD", "postgres_env"),
            ("DB_USER", "DB_PASSWORD", "db_env"),
            ("EPIDEMIC_DB_USER", "EPIDEMIC_DB_PASSWORD", "epidemic_env"),
            ("AHR1004_DATABASE_USER", "AHR1004_DATABASE_PASSWORD", "ahr1004_env"),
        ]:
            user = os.environ.get(user_var)
            password = os.environ.get(pass_var)
            if password:
                out.append((label, base.set(username=user or base.username, password=password)))
    dedup: list[tuple[str, URL]] = []
    seen: set[tuple[str, str | None]] = set()
    for label, url in out:
        key = (label, url.username)
        if key not in seen:
            seen.add(key)
            dedup.append((label, url))
    return dedup


def test_url(label: str, url: URL) -> dict[str, Any]:
    try:
        engine = create_engine(url.render_as_string(hide_password=False), future=True, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT current_user, current_database(), pg_is_in_recovery(), current_setting('transaction_read_only')")
            ).one()
            tables = conn.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('epidemic','epidemic_p1_12')")
            ).scalar_one()
            can_select = conn.execute(text("SELECT count(*) FROM epidemic.forecast_runs")).scalar_one()
        return {
            "label": label,
            "username": url.username,
            "ok": True,
            "current_user": row[0],
            "database": row[1],
            "read_only": row[3],
            "epidemic_table_count": int(tables),
            "forecast_runs_count": int(can_select),
        }
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "username": url.username, "ok": False, "error_type": type(exc).__name__, "error_tail": str(exc).splitlines()[0][:160]}


def main() -> None:
    results = [test_url(label, url) for label, url in candidate_urls()]
    extras: dict[str, Any] = {}
    ok_url = next((url for label, url in candidate_urls() if test_url(label, url).get("ok")), None)
    if ok_url is not None:
        try:
            engine = create_engine(ok_url.render_as_string(hide_password=False), future=True, pool_pre_ping=True)
            with engine.connect() as conn:
                for col in ["status", "onboarding_mode", "authorization_scope", "trust_tier", "authorization_origin"]:
                    extras[col] = [row[0] for row in conn.execute(text(f"SELECT DISTINCT {col} FROM epidemic_p1_12.trusted_source_contracts ORDER BY {col}")).all()]
                extras["constraints"] = [
                    {"name": row[0], "definition": row[1]}
                    for row in conn.execute(
                        text(
                            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='epidemic_p1_12.trusted_source_contracts'::regclass ORDER BY conname"
                        )
                    ).all()
                ]
        except Exception as exc:  # noqa: BLE001
            extras["error"] = {"type": type(exc).__name__, "detail": str(exc)[:200]}
    print(json.dumps({"results": results, "trusted_contract_enums": extras}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

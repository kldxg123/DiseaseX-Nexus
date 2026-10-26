from __future__ import annotations

import contextlib
import json
import os
import secrets
import shlex
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

EXPECTED_ROLE = "epidemicreview"


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"MISSING_REQUIRED_ENV:{name}")
    return value


def write_private(path: Path, payload: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> None:
    config = Path(required("REVIEW_CONFIG")).resolve(strict=True)
    original_bytes = config.read_bytes()
    original_url = make_url(required("REVIEW_DATABASE_URL"))
    if original_url.username != EXPECTED_ROLE or not original_url.database:
        raise RuntimeError("UNEXPECTED_REVIEW_DATABASE_IDENTITY")

    password = secrets.token_urlsafe(48)
    admin_connection = psycopg.connect(
        host="127.0.0.1",
        port=int(required("POSTGRES_PORT")),
        dbname=required("POSTGRES_DB"),
        user=required("POSTGRES_ADMIN_USER"),
        password=required("POSTGRES_ADMIN_PASSWORD"),
        connect_timeout=10,
    )
    changed = False
    try:
        with admin_connection.transaction(), admin_connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(EXPECTED_ROLE), sql.Literal(password))
            )
        changed = True
        review_url = (
            original_url.set(host="127.0.0.1", port=int(required("POSTGRES_PORT")), password=password)
            .difference_update_query(["host"])
            .render_as_string(hide_password=False)
        )
        write_private(config, f"REVIEW_DATABASE_URL={shlex.quote(review_url)}\n".encode())
        with (
            psycopg.connect(
                review_url.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=10
            ) as review_connection,
            review_connection.cursor() as cursor,
        ):
            cursor.execute("SELECT current_user, current_database()")
            identity = cursor.fetchone()
        if identity != (EXPECTED_ROLE, original_url.database):
            raise RuntimeError("REVIEW_DATABASE_VERIFICATION_FAILED")
    except BaseException:
        if changed:
            with admin_connection.transaction(), admin_connection.cursor() as cursor:
                cursor.execute(sql.SQL("ALTER ROLE {} PASSWORD NULL").format(sql.Identifier(EXPECTED_ROLE)))
        write_private(config, original_bytes)
        raise
    finally:
        admin_connection.close()
        password = ""

    print(json.dumps({"review_database_access_configured": True, "credential_value_emitted": False}))


if __name__ == "__main__":
    main()

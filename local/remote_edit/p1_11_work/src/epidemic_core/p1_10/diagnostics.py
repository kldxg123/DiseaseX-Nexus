from __future__ import annotations

import html
import logging
import os
import re
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

# Keep the P1-10R public constants import-compatible for older diagnostics clients.
INTERFACE_VERSION = "p1-10r-v1"
REQUIRED_REVISION = "0019_p1_10r_formal_preview_http500"
ACTIVE_INTERFACE_VERSION = "p1-10r3-v1"
ACTIVE_REQUIRED_REVISION = "0021_p1_11_ebola_monitoring"
COMPATIBLE_REVISIONS = frozenset(
    {
        "0020_p1_10r3_preview_permissions",
        ACTIVE_REQUIRED_REVISION,
    }
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
logger = logging.getLogger("epidemic.p1_10r")


def request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    value = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
    request.state.request_id = value
    return value


def short_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))[:12]


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, DBAPIError):
        sqlstate = str(getattr(getattr(exc, "orig", None), "sqlstate", ""))
        if sqlstate == "42501":
            return "PREVIEW_SCHEMA_PERMISSION_DENIED"
        if sqlstate == "42703":
            return "ROUTE_CONTRACT_MISMATCH"
        if sqlstate == "42P01":
            return "PREVIEW_SCHEMA_MISSING"
        if sqlstate == "25P02":
            return "TRANSACTION_ABORTED"
    return "UNKNOWN_INTERNAL_ERROR"


def unexpected_error(request: Request, exc: BaseException, *, review_console: bool) -> Response:
    code = classify_exception(exc)
    rid = short_request_id(request)
    logger.exception(
        "unhandled_request request_id=%s method=%s path=%s error_code=%s exception_type=%s",
        rid,
        request.method,
        request.url.path,
        code,
        type(exc).__name__,
    )
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"error_code": code, "request_id": rid, "message": "服务内部错误；请凭 request ID 联系管理员。"},
            status_code=500,
        )
    review_link = "/discoveries" if review_console else "http://127.0.0.1:8121/discoveries"
    retry_link = html.escape(request.url.path, quote=True)
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>请求失败</title></head><body><main>"
        "<h1>请求未完成</h1><p>系统已安全记录该错误，没有显示内部 traceback。</p>"
        f"<p>安全错误码：<code>{html.escape(code)}</code></p><p>request ID：<code>{html.escape(rid)}</code></p>"
        f"<p><a href='{retry_link}'>重试</a> "
        f"<a href='{html.escape(review_link, quote=True)}'>返回 Source Review Console</a></p>"
        "</main></body></html>",
        status_code=500,
    )


def safe_business_error(code: str, request: Request, status_code: int, *, review_console: bool) -> Response:
    rid = short_request_id(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error_code": code, "request_id": rid}, status_code=status_code)
    review_link = "/discoveries" if review_console else "http://127.0.0.1:8121/discoveries"
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>操作未执行</title></head><body><main>"
        "<h1>操作未执行</h1>"
        f"<p>安全错误码：<code>{html.escape(code)}</code></p><p>request ID：<code>{html.escape(rid)}</code></p>"
        f"<p><a href='{html.escape(review_link, quote=True)}'>返回 Source Review Console</a></p>"
        "</main></body></html>",
        status_code=status_code,
    )


def readiness(
    engine: Engine,
    *,
    expected_role: str,
    required_views: Sequence[str],
    required_privileges: Sequence[tuple[str, str, str]] = (),
    forbidden_privileges: Sequence[tuple[str, str, str]] = (),
) -> tuple[dict[str, Any], int]:
    environment = os.environ.get("APP_ENV", "production")
    deployment_commit = os.environ.get("EPIDEMIC_DEPLOYMENT_COMMIT", "")
    runtime_interface = os.environ.get("EPIDEMIC_INTERFACE_VERSION", "")
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    try:
        with engine.connect() as connection:
            current = connection.execute(text("SELECT current_user,current_database()"))
            role, database = current.one()
            rows = (
                connection.execute(
                    text(
                        "SELECT alembic_revision,environment,database_name,production,disposable,"
                        "required_objects_present FROM epidemic_preview.runtime_readiness_v1 "
                        "WHERE environment=:environment AND database_name=current_database()"
                    ),
                    {"environment": environment},
                )
                .mappings()
                .all()
            )
            marker = dict(rows[0]) if rows else {}
            checks["role"] = str(role) == expected_role
            checks["database_marker"] = bool(marker) and (
                (environment == "production" and bool(marker.get("production")) and not bool(marker.get("disposable")))
                or (environment != "production" and bool(marker.get("disposable")))
            )
            checks["migration"] = marker.get("alembic_revision") in COMPATIBLE_REVISIONS
            checks["schema_objects"] = bool(marker.get("required_objects_present"))
            checks["schema_usage"] = bool(
                connection.execute(
                    text("SELECT has_schema_privilege(current_user,'epidemic_preview','USAGE')")
                ).scalar_one()
            )
            for view in required_views:
                checks[f"select:{view}"] = bool(
                    connection.execute(
                        text("SELECT has_table_privilege(current_user,:view,'SELECT')"), {"view": view}
                    ).scalar_one()
                )
            for kind, target, privilege in required_privileges:
                if kind == "table":
                    query = "SELECT has_table_privilege(current_user,:target,:privilege)"
                elif kind == "sequence":
                    query = "SELECT has_sequence_privilege(current_user,:target,:privilege)"
                elif kind == "function":
                    query = "SELECT has_function_privilege(current_user,:target,:privilege)"
                else:
                    raise ValueError("UNKNOWN_READINESS_PRIVILEGE_KIND")
                checks[f"{kind}:{privilege}:{target}"] = bool(
                    connection.execute(text(query), {"target": target, "privilege": privilege}).scalar_one()
                )
            for kind, target, privilege in forbidden_privileges:
                if kind == "table":
                    schema_name, relation_name = target.split(".", 1)
                    query = (
                        "SELECT COALESCE((SELECT has_table_privilege(current_user,c.oid,:privilege) "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=:schema_name AND c.relname=:relation_name),false)"
                    )
                    params = {
                        "schema_name": schema_name,
                        "relation_name": relation_name,
                        "privilege": privilege,
                    }
                elif kind == "schema":
                    query = "SELECT has_schema_privilege(current_user,:target,:privilege)"
                    params = {"target": target, "privilege": privilege}
                else:
                    raise ValueError("UNKNOWN_READINESS_FORBIDDEN_KIND")
                checks[f"forbidden:{kind}:{privilege}:{target}"] = not bool(
                    connection.execute(text(query), params).scalar_one()
                )
            details = {"database": str(database), "environment": environment, "role": str(role)}
    except Exception as exc:  # readiness must fail closed
        logger.exception("readiness_failed exception_type=%s", type(exc).__name__)
        checks["database_contract"] = False
        details = {"environment": environment, "error_code": classify_exception(exc)}
    checks["interface_version"] = runtime_interface in {ACTIVE_INTERFACE_VERSION, INTERFACE_VERSION}
    checks["deployment_commit"] = bool(_COMMIT.fullmatch(deployment_commit))
    ready = bool(checks) and all(checks.values())
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        details["error_code"] = "PREVIEW_SCHEMA_PERMISSION_DENIED"
        details["failed_checks"] = failed
    return (
        {
            "ready": ready,
            "service_interface": ACTIVE_INTERFACE_VERSION,
            "required_revision": ACTIVE_REQUIRED_REVISION,
            "checks": checks,
            "details": details,
        },
        200 if ready else 503,
    )

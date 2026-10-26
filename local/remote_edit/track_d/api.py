"""KG Qwen autotuning API (kg_qwen).

Read endpoints go through the existing read-only SQLAlchemy engine; the write
endpoints use the kg_r3c ``write_conn()`` unix-socket connection and the same
``Idempotency-Key`` + ``kg_api_audit_log`` replay contract as the other KG
write APIs, plus a ``pg_advisory_xact_lock`` so concurrent same-key posts
serialize into exactly one semantic run. Everything here is Shadow-only: no
promotion, no active-pointer change, no trusted-table mutation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine, text

from epidemic_core.kg_qwen import pipeline as _pipeline

SCHEMA = "epidemic_kg"
AUDIT_ACTOR = "dashboard_user"
MAX_TRIAL_ROWS = 500
TRACK_KEYS = (
    _pipeline.TRACK_A_KEY,
    _pipeline.TRACK_B_KEY,
    _pipeline.TRACK_C_KEY,
    _pipeline.TRACK_D_KEY,
)

# Lazily resolved from epidemic_core.kg_r3c.dashboard_app (kept importable
# without psycopg / the runtime tree); tests monkeypatch this module attribute.
write_conn: Callable[[], Any] | None = None

LIMITATIONS_ZH = _pipeline.LIMITATIONS


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__str__") and value.__class__.__module__ == "decimal":
        return float(value)
    return value


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(payload) or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _resolve_write_conn() -> Callable[[], Any]:
    global write_conn
    if write_conn is None:
        from epidemic_core.kg_r3c.dashboard_app import write_conn as factory

        write_conn = factory
    return write_conn


class _ReadResult:
    """psycopg-style result surface over a SQLAlchemy mappings result."""

    def __init__(self, mappings: Any) -> None:
        self._mappings = mappings

    def fetchone(self) -> Any:
        return self._mappings.first()

    def fetchall(self) -> Any:
        return self._mappings.all()


class _ReadConn:
    """Adapt the read-only SQLAlchemy connection to the psycopg-ish
    ``conn.execute(sql, %s-params)`` surface the pipeline helpers expect."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Any = ()) -> _ReadResult:
        result = self._connection.exec_driver_sql(sql, tuple(params or ()))
        return _ReadResult(result.mappings())


def _row(connection: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = connection.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def install_autotuning_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    json_response: Callable[[Any, int], Response],
) -> None:
    def _replay(audit_id: str, extra: dict[str, Any] | None = None) -> JSONResponse:
        body: dict[str, Any] = {"idempotent_replay": True, "audit_id": audit_id, "status": "IDEMPOTENT_REPLAY"}
        if extra:
            body.update(extra)
        return JSONResponse(_jsonable(body), status_code=200, headers={"Idempotent-Replay": "true"})

    def _replay_run(audit_id: str, idempotency_key: str) -> JSONResponse:
        """Audit-replay response for /run enriched with the existing run id,
        read back through the deterministic idempotency-key lookup (never
        guessed via max(created_at))."""
        extra = None
        try:
            with engine.connect() as connection:
                row = _row(
                    connection,
                    f"""
                    select run_id::text, track_key, status, target_status, snapshot_sha256, split_sha256
                    from {SCHEMA}.kg_qwen_agent_runs where idempotency_key = :idem
                    """,
                    {"idem": idempotency_key},
                )
            if row:
                extra = {
                    "run_id": row.get("run_id"),
                    "track_key": row.get("track_key"),
                    "run_status": row.get("status"),
                    "target_status": row.get("target_status"),
                    "snapshot_sha256": row.get("snapshot_sha256"),
                    "split_sha256": row.get("split_sha256"),
                }
        except Exception:  # enrichment is best-effort; the slim body is the contract floor
            extra = None
        return _replay(audit_id, extra)

    def _audit_replay(conn: Any, endpoint: str, idempotency_key: str | None) -> str | None:
        if not idempotency_key:
            return None
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT audit_id FROM {SCHEMA}.kg_api_audit_log WHERE endpoint=%s AND idempotency_key=%s",
                (endpoint, idempotency_key),
            )
            row = cur.fetchone()
        return str(row["audit_id"]) if row else None

    def _audit_record(
        conn: Any, endpoint: str, method: str, idempotency_key: str | None, request_hash: str, response_status: int
    ) -> str:
        audit_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {SCHEMA}.kg_api_audit_log
                    (audit_id, endpoint, method, actor, idempotency_key, request_hash, response_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (endpoint, idempotency_key) DO NOTHING""",
                (audit_id, endpoint, method, AUDIT_ACTOR, idempotency_key, request_hash, response_status),
            )
        return audit_id

    # ------------------------------------------------------------- reads
    @app.get("/api/v1/kg/autotuning/overview")
    def kg_autotuning_overview(request: Request, track_key: str = _pipeline.TRACK_A_KEY) -> Response:
        if not authorized(request):
            return unauthorized()
        if track_key not in TRACK_KEYS:
            return json_response({"detail": "KQ_UNKNOWN_TRACK"}, 422)
        with engine.connect() as connection:
            base = _pipeline.autotuning_overview(_ReadConn(connection), track_key)
        if base is None:
            return json_response({"detail": "KQ_AUTOTUNING_NOT_FOUND"}, 404)
        return json_response(_jsonable(base), 200)

    @app.get("/api/v1/kg/autotuning/plans")
    def kg_autotuning_plans(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            plans = _pipeline.autotuning_plans(_ReadConn(connection), run_id)
        if not plans:
            return json_response({"detail": "KQ_RUN_NOT_FOUND"}, 404)
        return json_response(_jsonable({"run_id": run_id, "plans": plans, "limitations": LIMITATIONS_ZH}), 200)

    @app.get("/api/v1/kg/autotuning/rounds")
    def kg_autotuning_rounds(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            rounds = _pipeline.autotuning_rounds(_ReadConn(connection), run_id)
        if not rounds:
            return json_response({"detail": "KQ_RUN_NOT_FOUND"}, 404)
        return json_response(_jsonable({"run_id": run_id, "rounds": rounds, "limitations": LIMITATIONS_ZH}), 200)

    @app.get("/api/v1/kg/autotuning/trials")
    def kg_autotuning_trials(request: Request, run_id: str, round_index: int | None = None) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            trials = _pipeline.autotuning_trials(_ReadConn(connection), run_id, round_index)
        if not trials:
            return json_response({"detail": "KQ_RUN_NOT_FOUND"}, 404)
        return json_response(
            _jsonable(
                {
                    "run_id": run_id,
                    "round_index": round_index,
                    "trials": trials[:MAX_TRIAL_ROWS],
                    "truncated": len(trials) > MAX_TRIAL_ROWS,
                    "limitations": LIMITATIONS_ZH,
                }
            ),
            200,
        )

    @app.get("/api/v1/kg/autotuning/evaluation")
    def kg_autotuning_evaluation(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            evaluation = _pipeline.autotuning_evaluation(_ReadConn(connection), run_id)
        if evaluation is None:
            return json_response({"detail": "KQ_RUN_NOT_FOUND"}, 404)
        return json_response(_jsonable(evaluation), 200)

    @app.get("/api/v1/kg/autotuning/feedback")
    def kg_autotuning_feedback(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            feedback = _pipeline.autotuning_feedback(_ReadConn(connection), run_id)
        if feedback is None:
            return json_response({"detail": "KQ_RUN_NOT_FOUND"}, 404)
        return json_response(_jsonable(feedback), 200)

    # ------------------------------------------------------------ writes
    @app.post("/api/v1/kg/autotuning/run")
    def kg_autotuning_run(
        request: Request,
        payload: Annotated[dict[str, Any], Body(...)],
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        idem = (idempotency_key or "").strip()
        if not idem:
            return json_response({"detail": "IDEMPOTENCY_KEY_REQUIRED"}, 400)
        track_key = str((payload or {}).get("track_key") or "").strip()
        if track_key not in TRACK_KEYS:
            return json_response({"detail": "KQ_UNKNOWN_TRACK"}, 422)
        endpoint = "/api/v1/kg/autotuning/run"
        request_hash = _canonical_hash({"track_key": track_key})
        with _resolve_write_conn()() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"kg-autotuning-run:{idem}",))
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay_run(replay, idem)
            try:
                result = _pipeline.execute_autotuning(
                    conn, actor=AUDIT_ACTOR, idempotency_key=idem, track_key=track_key
                )
            except _pipeline.HiddenEvalConsumed as exc:
                # Global governance refusal: this data+window was already
                # consumed (or locked replay-only) by an earlier run. The
                # refusal itself is the audit record; nothing was evaluated.
                audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 409)
                conn.commit()
                return json_response(
                    {
                        "detail": str(exc).split(":", maxsplit=1)[0],
                        "reason": str(exc),
                        "track_key": track_key,
                        "audit_id": audit_id,
                        "status": "HELDOUT_ALREADY_CONSUMED",
                    },
                    409,
                )
            status_code = 200 if result.get("status") in ("COMPLETED", "IDEMPOTENT_REPLAY") else 409
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, status_code)
            conn.commit()
        return json_response(_jsonable({**result, "audit_id": audit_id}), status_code)

    @app.post("/api/v1/kg/autotuning/reconcile")
    def kg_autotuning_reconcile(
        request: Request,
        payload: Annotated[dict[str, Any], Body(...)],
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        idem = (idempotency_key or "").strip()
        if not idem:
            return json_response({"detail": "IDEMPOTENCY_KEY_REQUIRED"}, 400)
        run_id = str((payload or {}).get("run_id") or "").strip()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        endpoint = "/api/v1/kg/autotuning/reconcile"
        request_hash = _canonical_hash({"run_id": run_id})
        with _resolve_write_conn()() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"kg-autotuning-reconcile:{idem}",))
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay, {"run_id": run_id})
            result = _pipeline.reconcile_feedback(conn, actor=AUDIT_ACTOR, idempotency_key=idem, run_id=run_id)
            status_code = 200 if result.get("status") == "COMPLETED" else 409
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, status_code)
            conn.commit()
        return json_response(_jsonable({**result, "audit_id": audit_id}), status_code)

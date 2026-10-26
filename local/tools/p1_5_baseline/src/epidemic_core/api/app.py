from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from epidemic_core.api.auth import require_auth
from epidemic_core.api.middleware import BodyLimitMiddleware, SecurityHeadersMiddleware
from epidemic_core.api.routes.queries import router
from epidemic_core.api.routes.retrievals import router as retrieval_router
from epidemic_core.api.settings import ApiSettings
from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.db.session import session_factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    cfg = ApiSettings()  # type: ignore[call-arg]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = Settings(APP_DATABASE_URL=cfg.APP_DATABASE_URL)
        engine = create_app_engine(settings)
        app.state.engine = engine
        app.state.session_factory = session_factory(engine)
        app.state.api_token = cfg.EPIDEMIC_QUERY_API_TOKEN.get_secret_value()
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(require_auth)]
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodyLimitMiddleware, max_bytes=8192)
    app.include_router(router)
    app.include_router(retrieval_router)

    @app.exception_handler(RequestValidationError)
    def validation(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "INVALID_REQUEST", "message": "Request validation failed"}}, status_code=422
        )

    @app.exception_handler(HTTPException)
    def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "INTERNAL_ERROR", "message": "Request failed"}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(SQLAlchemyError)
    def db_error(_request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "DATABASE_UNAVAILABLE", "message": "Database unavailable"}}, status_code=503
        )

    @app.exception_handler(Exception)
    def internal(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "Internal error"}}, status_code=500)

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz")
    def readyz(request: Request) -> dict[str, bool]:
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=503, detail={"code": "DATABASE_UNAVAILABLE", "message": "Database unavailable"}
            ) from exc
        return {"ready": True}

    return app

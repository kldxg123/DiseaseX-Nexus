from __future__ import annotations

from sqlalchemy import Engine, create_engine, event

from epidemic_core.config import Settings


def create_app_engine(settings: Settings) -> Engine:
    if settings.APP_DATABASE_URL is None:
        raise ValueError("APP_DATABASE_URL is required")
    engine = create_engine(
        settings.APP_DATABASE_URL.get_secret_value(),
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        echo=False,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def _secure_session(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SET search_path TO pg_catalog")
            cursor.execute("SET timezone TO 'UTC'")
        finally:
            cursor.close()

    return engine

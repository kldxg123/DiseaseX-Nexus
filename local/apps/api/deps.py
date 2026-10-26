from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from common.config import get_settings
from common.db import get_session


def get_database_url() -> str:
    return get_settings().database_url


def get_db_session() -> Iterator[Session]:
    with get_session(get_database_url()) as session:
        yield session


def get_db() -> Iterator[Session]:
    yield from get_db_session()

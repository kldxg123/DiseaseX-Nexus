from __future__ import annotations

from collections.abc import Generator

from fastapi import HTTPException,Request
from sqlalchemy.exc import DBAPIError,OperationalError
from sqlalchemy.orm import Session


def get_session(request:Request)->Generator[Session,None,None]:
    session=request.app.state.session_factory()
    try:yield session
    except (OperationalError,DBAPIError) as exc:
        session.rollback();raise HTTPException(status_code=503,detail={"code":"DATABASE_UNAVAILABLE","message":"Database unavailable"}) from exc
    finally:session.close()

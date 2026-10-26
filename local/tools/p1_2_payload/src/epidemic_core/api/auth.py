from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer=HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def require_auth(request:Request,credentials:BearerCredentials)->None:
    expected=request.app.state.api_token
    valid=credentials is not None and credentials.scheme.lower()=="bearer" and secrets.compare_digest(credentials.credentials,expected)
    if not valid:raise HTTPException(status_code=401,detail={"code":"AUTH_REQUIRED","message":"Authentication required"})

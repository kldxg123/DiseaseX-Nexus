from __future__ import annotations

import logging
import time
import uuid
from typing import Any, cast

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

log=logging.getLogger("epidemic_query_api")


class BodyLimitMiddleware:
    def __init__(self,app:Any,max_bytes:int=8192)->None:self.app=app;self.max_bytes=max_bytes
    async def __call__(self,scope:dict[str,Any],receive:Any,send:Any)->None:
        if scope["type"]!="http":await self.app(scope,receive,send);return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = self.max_bytes + 1
        if declared > self.max_bytes:
            await self._reject(scope, receive, send)
            return
        seen=0
        messages: list[dict[str, Any]] = []
        while True:
            message=cast(dict[str,Any],await receive())
            messages.append(message)
            if message["type"]!="http.request":break
            seen+=len(message.get("body",b""))
            if seen>self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body",False):break
        index=0
        async def replay()->dict[str,Any]:
            nonlocal index
            if index<len(messages):
                message=messages[index];index+=1;return message
            return {"type":"http.request","body":b"","more_body":False}
        await self.app(scope,replay,send)

    async def _reject(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        response=JSONResponse({"error":{"code":"PAYLOAD_TOO_LARGE","message":"Request body exceeds limit"}},status_code=413,headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","X-Request-ID":str(uuid.uuid4())})
        await response(scope,receive,send)
class SecurityHeadersMiddleware:
    def __init__(self,app:Any)->None:self.app=app
    async def __call__(self,scope:dict[str,Any],receive:Any,send:Any)->None:
        if scope["type"]!="http":await self.app(scope,receive,send);return
        request_id=str(uuid.uuid4());start=time.monotonic();status=500
        async def secured(message:dict[str,Any])->None:
            nonlocal status
            if message["type"]=="http.response.start":
                status=message["status"];headers=MutableHeaders(scope=message);headers["cache-control"]="no-store";headers["x-content-type-options"]="nosniff";headers["x-request-id"]=request_id
            await send(message)
        await self.app(scope,receive,secured)
        route=scope.get("route");template=getattr(route,"path","unmatched")
        log.info("request_id=%s method=%s route=%s status=%s duration_ms=%d",request_id,scope.get("method"),template,status,int((time.monotonic()-start)*1000))

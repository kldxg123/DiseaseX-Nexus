from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import os
import random
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from epidemic_core.retrieval.registry import SourceSpec, WHO_DOWNLOAD_URL


class DownloadError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(code);self.code=code;self.status=status;self.retryable=retryable


@dataclass(frozen=True)
class Attempt:
    number: int;http_status: int | None;error_code: str | None;etag: str | None;last_modified: str | None;content_type: str | None;byte_size: int | None


@dataclass(frozen=True)
class DownloadResult:
    http_status: int;sha256: str | None;byte_size: int | None;media_type: str | None;etag: str | None;last_modified: str | None;path: Path | None;not_modified: bool;attempt_number: int


def validate_public_dns(host: str, resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo) -> None:
    records=resolver(host,443,type=socket.SOCK_STREAM)
    if not records:raise DownloadError("DNS_UNRESOLVED")
    for record in records:
        ip=ipaddress.ip_address(record[4][0])
        if (not ip.is_global or ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified):raise DownloadError("UNSAFE_DNS_ADDRESS")


class WhoDownloader:
    def __init__(self,spec:SourceSpec,raw_root:Path,*,transport:httpx.BaseTransport|None=None,sleep:Callable[[float],None]=time.sleep,rng:Callable[[],float]=random.random,resolver:Callable[...,Sequence[tuple[Any,...]]]=socket.getaddrinfo):
        self.spec=spec;self.raw_root=raw_root;self.transport=transport;self.sleep=sleep;self.rng=rng;self.resolver=resolver
        parsed=urlsplit(WHO_DOWNLOAD_URL)
        if spec.download_url!=WHO_DOWNLOAD_URL or parsed.scheme!="https" or parsed.hostname!="srhdpeuwpubsa.blob.core.windows.net" or parsed.port not in (None,443) or parsed.path!="/whdh/COVID/WHO-COVID-19-global-data.csv" or parsed.query:raise DownloadError("UNSAFE_TARGET")

    def fetch(self,*,etag:str|None=None,last_modified:str|None=None,on_attempt:Callable[[Attempt],None]|None=None)->DownloadResult:
        validate_public_dns("srhdpeuwpubsa.blob.core.windows.net",self.resolver)
        headers={"User-Agent":"epidemic-openclaw/1.0 controlled-who-retrieval","Accept":"text/csv,application/octet-stream","Accept-Encoding":"identity"}
        if etag and "\r" not in etag and "\n" not in etag:headers["If-None-Match"]=etag
        if last_modified and "\r" not in last_modified and "\n" not in last_modified:headers["If-Modified-Since"]=last_modified
        retry_status={408,425,429,500,502,503,504};last_error:DownloadError|None=None
        for number in range(1,4):
            try:
                result=self._once(number,headers)
                if on_attempt:on_attempt(Attempt(number,result.http_status,None,result.etag,result.last_modified,result.media_type,result.byte_size))
                return result
            except DownloadError as exc:
                last_error=exc
                if on_attempt:on_attempt(Attempt(number,exc.status,exc.code,None,None,None,None))
                if number>=3 or not (exc.retryable or exc.status in retry_status):raise
                self.sleep(min(120.0,2**(number-1)+self.rng()))
        raise last_error or DownloadError("DOWNLOAD_FAILED")

    def _once(self,number:int,headers:dict[str,str])->DownloadResult:
        timeout=httpx.Timeout(connect=10,read=60,write=10,pool=10)
        try:
            with httpx.Client(transport=self.transport,trust_env=False,follow_redirects=False,timeout=timeout) as client:
                with client.stream("GET",WHO_DOWNLOAD_URL,headers=headers) as response:
                    status=response.status_code;etag=response.headers.get("etag");modified=response.headers.get("last-modified")
                    if 300<=status<400 and status!=304:raise DownloadError("UNSAFE_REDIRECT",status=status)
                    if status==304:return DownloadResult(status,None,None,None,etag,modified,None,True,number)
                    if status!=200:raise DownloadError("HTTP_STATUS",status=status,retryable=status in {408,425,429,500,502,503,504})
                    if response.headers.get("content-encoding","").lower() not in ("","identity"):raise DownloadError("UNEXPECTED_CONTENT_ENCODING",status=status)
                    media=response.headers.get("content-type","application/octet-stream").split(";",1)[0].strip().lower()
                    if media not in self.spec.allowed_media_types:raise DownloadError("UNEXPECTED_CONTENT_TYPE",status=status)
                    declared=response.headers.get("content-length")
                    if declared and int(declared)>self.spec.maximum_bytes:raise DownloadError("RESPONSE_TOO_LARGE",status=status)
                    self.raw_root.mkdir(parents=True,exist_ok=True);tmp_dir=self.raw_root/".tmp";tmp_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
                    fd,name=tempfile.mkstemp(prefix="who-",suffix=".part",dir=tmp_dir);os.chmod(name,0o600);digest=hashlib.sha256();size=0;prefix=b""
                    try:
                        with os.fdopen(fd,"wb") as handle:
                            for chunk in response.iter_bytes():
                                size+=len(chunk)
                                if size>self.spec.maximum_bytes:raise DownloadError("RESPONSE_TOO_LARGE",status=status)
                                digest.update(chunk);handle.write(chunk)
                                if len(prefix)<4096:prefix=(prefix+chunk)[:4096]
                            handle.flush();os.fsync(handle.fileno())
                        self._validate(prefix)
                        sha=digest.hexdigest();target=self.raw_root/sha[:2]/(sha+".csv");target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                        if target.exists():
                            if target.stat().st_size!=size or self._hash(target)!=sha:raise DownloadError("ARTIFACT_COLLISION")
                            os.unlink(name)
                        else:os.replace(name,target);dir_fd=os.open(target.parent,os.O_DIRECTORY);os.fsync(dir_fd);os.close(dir_fd)
                        return DownloadResult(status,sha,size,media,etag,modified,target,False,number)
                    except Exception:
                        with contextlib.suppress(FileNotFoundError):os.unlink(name)
                        raise
        except httpx.TimeoutException as exc:raise DownloadError("HTTP_TIMEOUT",retryable=True) from exc
        except httpx.TransportError as exc:raise DownloadError("HTTP_TRANSPORT",retryable=True) from exc

    def _validate(self,prefix:bytes)->None:
        if b"\x00" in prefix:raise DownloadError("INVALID_NUL_CONTENT")
        text=prefix.decode("utf-8-sig",errors="strict").lstrip()
        if text[:20].lower().startswith(("<!doctype html","<html")):raise DownloadError("HTML_RESPONSE")
        first=text.splitlines()[0].rstrip("\r") if text.splitlines() else ""
        if first!=self.spec.expected_header:raise DownloadError("INVALID_CSV_HEADER")

    @staticmethod
    def _hash(path:Path)->str:
        h=hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
        return h.hexdigest()

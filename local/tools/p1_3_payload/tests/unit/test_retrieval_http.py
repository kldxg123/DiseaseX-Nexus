from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from epidemic_core.retrieval.http import DownloadError, WhoDownloader, validate_public_dns
from epidemic_core.retrieval.registry import load_registry

REGISTRY=Path(__file__).parents[2]/"reference/data_sources.v1.json"
HEADER=b"Date_reported,Country_code,Country,WHO_region,New_cases,Cumulative_cases,New_deaths,Cumulative_deaths\r\n"
RAW=HEADER+b"2024-01-01,XK,Kosovo,EUR,,10,-2,3\r\n2024-01-08,XXF,Special,Other,1,11,,3\r\n"


def public_dns(*_args,**_kwargs):return [(2,1,6,"",("SERVER_HOST",443))]


def test_registry_canonical_hash_and_fixed_target():
    value,digest,spec=load_registry(REGISTRY);assert len(digest)==64 and spec.source_key=="who_covid19_global_weekly" and value["schema_version"]==1


def test_raw_bytes_octet_stream_content_addressed(tmp_path):
    def handler(request):return httpx.Response(200,headers={"Content-Type":"application/octet-stream","ETag":"v1","Last-Modified":"Wed, 01 Jan 2025 00:00:00 GMT"},content=RAW)
    _,_,spec=load_registry(REGISTRY);result=WhoDownloader(spec,tmp_path,transport=httpx.MockTransport(handler),resolver=public_dns).fetch()
    assert result.sha256==hashlib.sha256(RAW).hexdigest() and result.path.read_bytes()==RAW and b",," in RAW and b",-2," in RAW and b",XK," in RAW


@pytest.mark.parametrize(("body","code"),[(b"<html>failure</html>","HTML_RESPONSE"),(HEADER+b"x\x00y","INVALID_NUL_CONTENT"),(b"wrong,header\n1,2\n","INVALID_CSV_HEADER")])
def test_invalid_content(tmp_path,body,code):
    _,_,spec=load_registry(REGISTRY);downloader=WhoDownloader(spec,tmp_path,transport=httpx.MockTransport(lambda _:httpx.Response(200,headers={"Content-Type":"application/octet-stream"},content=body)),resolver=public_dns)
    with pytest.raises(DownloadError,match=code):downloader.fetch()


def test_redirect_and_private_dns_rejected(tmp_path):
    _,_,spec=load_registry(REGISTRY);d=WhoDownloader(spec,tmp_path,transport=httpx.MockTransport(lambda _:httpx.Response(302,headers={"Location":"https://example.com"})),resolver=public_dns)
    with pytest.raises(DownloadError,match="UNSAFE_REDIRECT"):d.fetch()
    for address in ("127.0.0.1","SERVER_HOST","SERVER_HOST","SERVER_HOST","0.0.0.0"):
        with pytest.raises(DownloadError,match="UNSAFE_DNS_ADDRESS"):validate_public_dns("fixed",lambda *_a,address=address,**_k:[(2,1,6,"",(address,443))])


def test_retries_and_304(tmp_path):
    calls=[]
    def handler(_request):
        calls.append(1);return httpx.Response(503) if len(calls)<3 else httpx.Response(200,headers={"Content-Type":"text/csv"},content=RAW)
    _,_,spec=load_registry(REGISTRY);result=WhoDownloader(spec,tmp_path,transport=httpx.MockTransport(handler),resolver=public_dns,sleep=lambda _:None,rng=lambda:0).fetch();assert result.http_status==200 and len(calls)==3
    result=WhoDownloader(spec,tmp_path,transport=httpx.MockTransport(lambda _:httpx.Response(304)),resolver=public_dns).fetch(etag="v1");assert result.not_modified and result.path is None


def test_actual_stream_limit_beats_content_length(tmp_path):
    _,_,base=load_registry(REGISTRY);spec=base.__class__(**{**base.__dict__,"maximum_bytes":len(HEADER)+2})
    d=WhoDownloader(spec,tmp_path,transport=httpx.MockTransport(lambda _:httpx.Response(200,headers={"Content-Type":"text/csv","Content-Length":"1"},content=RAW)),resolver=public_dns)
    with pytest.raises(DownloadError,match="RESPONSE_TOO_LARGE"):d.fetch()

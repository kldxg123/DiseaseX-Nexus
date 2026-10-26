from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from epidemic_core.retrieval.dengue import DengueDownloader, build_params, validate_envelope
from epidemic_core.retrieval.http import DownloadError
from epidemic_core.retrieval.registry import load_registry, spec_for_source

REGISTRY=Path(__file__).parents[2]/"reference/data_sources.v2.json"
def dns(*_a,**_k):return [(2,1,6,"",("SERVER_HOST",443))]
def row(**changes):
 value={"COUNTRY":"Brazil","ISO3":"BRA","WHO_REGION":"AMR","YEAR":2024,"DATE_NUM":1,"START_DATE":"2024-01-01","DATE_TYPE":"month","CASES":0,"CONFIRMED_CASES":None,"SEVERE_CASES":None,"DEATHS":-1,"SERO_1":"TRUE","SERO_2":"FALSE","SERO_3":None,"SERO_4":None,"POPULATION":1};value.update(changes);return value
def body(rows,count=None,**extra):return json.dumps({"@odata.context":"https://xmart-api-public.who.int/ARBOV/$metadata#V_DENGUE_GLOBAL_VALIDATED_PUBLIC","@odata.count":len(rows) if count is None else count,"value":rows,**extra},separators=(",",":")).encode()

def test_fixed_params_reject_injection():
 p=build_params("BRA",dt.date(2024,1,1),dt.date(2024,4,30));assert set(p)=={"$select","$filter","$orderby","$top","$count","excludeSysColumns"} and "$skip" not in p
 with pytest.raises(DownloadError):build_params("BRA' or 1 eq 1",dt.date(2024,1,1),dt.date(2024,1,2))

@pytest.mark.parametrize(("payload","code"),[(body([row()],2),"TRUNCATED_SOURCE_RESPONSE"),(body([row()],**{"@odata.nextLink":"x"}),"PAGINATION_REQUIRED_NOT_IMPLEMENTED"),(body([],10001),"SOURCE_RESULT_LIMIT_EXCEEDED"),(b"<html>x</html>","NON_JSON_RESPONSE"),(body([row(CASES=True)]),"SOURCE_SCHEMA_MISMATCH"),(body([row(DATE_TYPE="new")]),"UNKNOWN_DATE_TYPE")])
def test_envelope_fail_closed(payload,code):
 with pytest.raises(DownloadError,match=code):validate_envelope(payload,"BRA")

def test_download_content_addressed_and_null_zero(tmp_path):
 _,_,specs=load_registry(REGISTRY);spec=spec_for_source(specs,"who_dengue_global_validated");raw=body([row()])
 d=DengueDownloader(spec,tmp_path,transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={"Content-Type":"application/json","Date":"x"},content=raw)),resolver=dns,sleep=lambda _:None)
 result=d.fetch("BRA",dt.date(2024,1,1),dt.date(2024,4,30));assert result.odata_count==1 and result.path.suffix==".json" and result.path.read_bytes()==raw

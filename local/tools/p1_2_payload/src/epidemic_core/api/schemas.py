from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel,ConfigDict,Field


class ApiModel(BaseModel):model_config=ConfigDict(extra="forbid",strict=True)
class QueryInput(ApiModel):
    country:str=Field(min_length=1,max_length=128);disease:str=Field(min_length=1,max_length=128);start_date:str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$");end_date:str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
class CanonicalCountry(ApiModel):alpha2:str;alpha3:str;name:str
class CanonicalDisease(ApiModel):id:uuid.UUID;slug:str;name:str
class NormalizedResponse(ApiModel):
    country_alpha2:str;country_alpha3:str;country_name:str;disease_id:uuid.UUID;disease_slug:str;disease_name:str;start_date:dt.date;end_date:dt.date;fingerprint:str;schema_version:int;persisted:bool=False;retrieval_started:bool=False
class RegisteredResponse(NormalizedResponse):query_id:uuid.UUID;created:bool
class QueryResponse(ApiModel):
    query_id:uuid.UUID;fingerprint:str;country_alpha2:str;country_alpha3:str;country_name:str;disease_id:uuid.UUID;disease_slug:str;disease_name:str;start_date:dt.date;end_date:dt.date;schema_version:int;created_at:dt.datetime;retrieval_started:bool=False

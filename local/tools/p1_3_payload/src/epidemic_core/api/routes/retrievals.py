from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from epidemic_core.api.dependencies import get_session
from epidemic_core.api.schemas import RetrievalResponse, StartRetrievalBody
from epidemic_core.db.models.tables import DataSource, Disease, QueryRequest, RawArtifact, RetrievalRun, SourceFetch
from epidemic_core.retrieval.registry import NoCompatibleSource, RegistryError, load_registry
from epidemic_core.retrieval.service import RetrievalError, RetrievalService

router=APIRouter(tags=["retrievals"]);SessionDep=Annotated[Session,Depends(get_session)];REGISTRY=Path("USER_HOME/autodl-tmp/epidemic-openclaw/reference/data_sources.v1.json")


def response_for(session:Session,run:RetrievalRun,created:bool=False,retried:bool=False)->RetrievalResponse:
    query=session.get(QueryRequest,run.query_request_id);source=session.get(DataSource,run.source_id)
    if query is None or source is None:raise HTTPException(status_code=500,detail={"code":"INTERNAL_ERROR","message":"Internal error"})
    disease=session.get(Disease,query.disease_id);_,_,spec=load_registry(REGISTRY)
    fetch=session.execute(select(SourceFetch).where(SourceFetch.retrieval_run_id==run.id).order_by(SourceFetch.requested_at.desc())).scalars().first();artifact=session.get(RawArtifact,fetch.artifact_id) if fetch and fetch.artifact_id else None
    return RetrievalResponse(retrieval_run_id=run.id,query_id=query.id,source_key=source.source_key,status=run.status,attempt_number=run.attempt_number,created=created,retried=retried,created_at=run.created_at,started_at=run.started_at,finished_at=run.finished_at,error_code=run.error_code,not_modified=bool(fetch and fetch.http_status==304),artifact_scope="global",requested_country_alpha2=query.country_alpha2,requested_disease_slug=disease.slug if disease else "unknown",requested_start_date=query.start_date,requested_end_date=query.end_date,artifact_sha256=artifact.sha256 if artifact else None,artifact_byte_size=artifact.byte_size if artifact else None,artifact_media_type=artifact.media_type if artifact else None,attribution=spec.attribution,source_page_url=spec.source_page_url,query_filter_applied=False,normalized=False,normalized_observations_created=False)


@router.post("/v1/queries/{query_id}/retrievals",response_model=RetrievalResponse,status_code=202)
def start_retrieval(query_id:uuid.UUID,_body:StartRetrievalBody,response:Response,session:SessionDep)->RetrievalResponse:
    try:
        with session.begin_nested():result=RetrievalService(REGISTRY).enqueue(session,query_id)
        session.commit()
    except NoCompatibleSource as exc:raise HTTPException(status_code=422,detail={"code":"NO_COMPATIBLE_SOURCE","message":"No compatible source"}) from exc
    except RegistryError as exc:raise HTTPException(status_code=503,detail={"code":"SOURCE_REGISTRY_UNAVAILABLE","message":"Source registry unavailable"}) from exc
    except RetrievalError as exc:
        code=str(exc);status=404 if code=="QUERY_NOT_FOUND" else 409
        raise HTTPException(status_code=status,detail={"code":code,"message":"Retrieval could not be started"}) from exc
    if not result.created:response.status_code=200
    return response_for(session,result.run,result.created,result.retried)


@router.get("/v1/retrievals/{retrieval_run_id}",response_model=RetrievalResponse)
def get_retrieval(retrieval_run_id:uuid.UUID,session:SessionDep)->RetrievalResponse:
    run=session.get(RetrievalRun,retrieval_run_id)
    if run is None:raise HTTPException(status_code=404,detail={"code":"RETRIEVAL_NOT_FOUND","message":"Retrieval not found"})
    return response_for(session,run)

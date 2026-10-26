from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from epidemic_core.api.dependencies import get_session
from epidemic_core.api.schemas import RetrievalResponse, StartRetrievalBody
from epidemic_core.db.models.tables import (
    DataSource, DatasetSnapshot, Disease, NormalizationRun, QueryRequest, RawArtifact, RetrievalRun, SourceFetch,
)
from epidemic_core.normalization.contract import ContractError, load_contract
from epidemic_core.normalization.service import NormalizationError, NormalizationService
from epidemic_core.retrieval.registry import NoCompatibleSource, RegistryError, load_registry
from epidemic_core.retrieval.service import RetrievalError, RetrievalService

router = APIRouter(tags=["retrievals"])
SessionDep = Annotated[Session, Depends(get_session)]
REGISTRY = Path("USER_HOME/autodl-tmp/epidemic-openclaw/reference/data_sources.v1.json")
REFERENCE_ROOT = REGISTRY.parent


def response_for(session: Session, run: RetrievalRun, created: bool = False, retried: bool = False) -> RetrievalResponse:
    query = session.get(QueryRequest, run.query_request_id); source = session.get(DataSource, run.source_id)
    if query is None or source is None:
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "Internal error"})
    disease = session.get(Disease, query.disease_id); _, _, spec = load_registry(REGISTRY)
    fetch = session.execute(select(SourceFetch).where(SourceFetch.retrieval_run_id == run.id).order_by(SourceFetch.requested_at.desc())).scalars().first()
    artifact = session.get(RawArtifact, fetch.artifact_id) if fetch and fetch.artifact_id else None
    normalization = session.execute(select(NormalizationRun).where(NormalizationRun.retrieval_run_id == run.id).order_by(NormalizationRun.created_at.desc())).scalars().first()
    snapshot = session.get(DatasetSnapshot, normalization.dataset_snapshot_id) if normalization and normalization.dataset_snapshot_id else None
    if run.status != "succeeded": pipeline = "retrieval_" + run.status
    elif normalization is None: pipeline = "normalization_not_enqueued"
    elif normalization.status == "succeeded": pipeline = "ready" if snapshot else "no_observations"
    else: pipeline = "normalization_" + normalization.status
    normalized = bool(normalization and normalization.status == "succeeded")
    return RetrievalResponse(
        retrieval_run_id=run.id, query_id=query.id, source_key=source.source_key, status=run.status,
        retrieval_status=run.status, attempt_number=run.attempt_number, created=created, retried=retried,
        created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at, error_code=run.error_code,
        not_modified=bool(fetch and fetch.http_status == 304), artifact_scope="global",
        requested_country_alpha2=query.country_alpha2, requested_disease_slug=disease.slug if disease else "unknown",
        requested_start_date=query.start_date, requested_end_date=query.end_date,
        artifact_sha256=artifact.sha256 if artifact else None, artifact_byte_size=artifact.byte_size if artifact else None,
        artifact_media_type=artifact.media_type if artifact else None, attribution=spec.attribution,
        source_page_url=spec.source_page_url, terms_url=spec.terms_url, license_summary=spec.license_expression,
        query_filter_applied=normalized, normalized=normalized,
        normalized_observations_created=bool(normalization and normalization.normalized_observation_count),
        normalization_run_id=normalization.id if normalization else None,
        normalization_status=normalization.status if normalization else None, pipeline_status=pipeline,
        result_code=normalization.result_code if normalization else None,
        selected_source_rows=normalization.selected_source_rows if normalization else 0,
        normalized_observation_count=normalization.normalized_observation_count if normalization else 0,
        missing_value_count=normalization.missing_value_count if normalization else 0,
        quality_warning_count=normalization.warning_count if normalization else 0,
        dataset_snapshot_id=snapshot.id if snapshot else None, snapshot_status="ready" if snapshot else "absent",
        snapshot_row_count=snapshot.row_count if snapshot else 0,
        snapshot_content_sha256=snapshot.content_sha256 if snapshot else None,
        snapshot_schema_version=snapshot.snapshot_schema_version if snapshot else None,
        training_format_ready=snapshot is not None, forecast_readiness_assessed=False, prediction_performed=False,
    )


@router.post("/v1/queries/{query_id}/retrievals", response_model=RetrievalResponse, status_code=202)
def start_retrieval(query_id: uuid.UUID, _body: StartRetrievalBody, response: Response, session: SessionDep) -> RetrievalResponse:
    try:
        with session.begin_nested():
            result = RetrievalService(REGISTRY).enqueue(session, query_id)
            normalization_retried = False
            if result.run.status == "succeeded":
                existing = session.execute(select(NormalizationRun).where(NormalizationRun.retrieval_run_id == result.run.id)).scalar_one_or_none()
                if existing is not None:
                    normalization_retried = NormalizationService.retry_failed(session, result.run.id)
                else:
                    fetch = session.execute(select(SourceFetch).where(SourceFetch.retrieval_run_id == result.run.id, SourceFetch.artifact_id.is_not(None)).order_by(SourceFetch.requested_at.desc())).scalars().first()
                    artifact = session.get(RawArtifact, fetch.artifact_id) if fetch and fetch.artifact_id else None
                    if fetch is None or artifact is None:
                        raise NormalizationError("ARTIFACT_INTEGRITY_ERROR")
                    contract, _mapping = load_contract(session, REFERENCE_ROOT)
                    NormalizationService.enqueue(session, result.run, fetch, artifact.sha256, contract.versions)
        session.commit()
    except NoCompatibleSource as exc:
        raise HTTPException(status_code=422, detail={"code": "NO_COMPATIBLE_SOURCE", "message": "No compatible source"}) from exc
    except RegistryError as exc:
        raise HTTPException(status_code=503, detail={"code": "SOURCE_REGISTRY_UNAVAILABLE", "message": "Source registry unavailable"}) from exc
    except (RetrievalError, NormalizationError, ContractError) as exc:
        code = str(exc); status = 404 if code == "QUERY_NOT_FOUND" else 409
        raise HTTPException(status_code=status, detail={"code": code, "message": "Pipeline could not be started"}) from exc
    if not result.created: response.status_code = 200
    return response_for(session, result.run, result.created, result.retried or normalization_retried)


@router.get("/v1/retrievals/{retrieval_run_id}", response_model=RetrievalResponse)
def get_retrieval(retrieval_run_id: uuid.UUID, session: SessionDep) -> RetrievalResponse:
    run = session.get(RetrievalRun, retrieval_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RETRIEVAL_NOT_FOUND", "message": "Retrieval not found"})
    return response_for(session, run)

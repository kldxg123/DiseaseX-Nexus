from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from epidemic_core.api.dependencies import get_session
from epidemic_core.api.schemas import NormalizedResponse,QueryInput,QueryResponse,RegisteredResponse
from epidemic_core.db.models.tables import Country,Disease
from epidemic_core.db.repositories import QueryRepository
from epidemic_core.domain.query import EpidemicQueryInput, NormalizedEpidemicQuery
from epidemic_core.errors import EpidemicCoreError, ResolutionError
from epidemic_core.normalization import CountryResolver
from epidemic_core.query_service import QueryService

router=APIRouter(prefix="/v1/queries",tags=["queries"])
SessionDep = Annotated[Session, Depends(get_session)]


def details(session: Session, value: QueryInput) -> tuple[NormalizedEpidemicQuery, Country, Disease]:
    service = QueryService()
    try:
        normalized = service.normalize(session, EpidemicQueryInput(**value.model_dump()))
    except ResolutionError as exc:
        try:
            CountryResolver(service.countries.aliases(session)).resolve(value.country)
            entity = "DISEASE"
        except ResolutionError:
            entity = "COUNTRY"
        suffix = "AMBIGUOUS" if exc.code == "AMBIGUOUS" else "NOT_FOUND"
        status = 409 if suffix == "AMBIGUOUS" else 404
        raise HTTPException(
            status_code=status,
            detail={"code": f"{entity}_{suffix}", "message": "Input could not be resolved", "candidates": list(exc.candidates[:5])},
        ) from exc
    except EpidemicCoreError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_DATE_RANGE", "message": "Invalid date range"}) from exc
    country = session.execute(select(Country).where(Country.alpha2 == normalized.country_alpha2)).scalar_one()
    disease = session.execute(select(Disease).where(Disease.slug == normalized.disease_slug)).scalar_one()
    return normalized, country, disease


@router.post("/normalize",response_model=NormalizedResponse)
def normalize(value:QueryInput,session:SessionDep)->NormalizedResponse:
    normalized,country,disease=details(session,value);return NormalizedResponse(country_alpha2=country.alpha2,country_alpha3=country.alpha3,country_name=country.name,disease_id=disease.id,disease_slug=disease.slug,disease_name=disease.canonical_name,start_date=normalized.start_date,end_date=normalized.end_date,fingerprint=normalized.fingerprint(),schema_version=normalized.schema_version)


@router.post("",response_model=RegisteredResponse,status_code=201)
def register(value:QueryInput,response:Response,session:SessionDep)->RegisteredResponse:
    normalized,country,disease=details(session,value);repo=QueryRepository()
    with session.begin_nested():row,created=repo.create_or_get_result(session,value.country,value.disease,disease.id,normalized)
    session.commit()
    if not created:response.status_code=200
    return RegisteredResponse(query_id=row.id,created=created,country_alpha2=country.alpha2,country_alpha3=country.alpha3,country_name=country.name,disease_id=disease.id,disease_slug=disease.slug,disease_name=disease.canonical_name,start_date=row.start_date,end_date=row.end_date,fingerprint=row.fingerprint,schema_version=row.schema_version)


@router.get("/{query_id}",response_model=QueryResponse)
def get_query(query_id:uuid.UUID,session:SessionDep)->QueryResponse:
    row=QueryRepository().get_by_id(session,query_id)
    if row is None:raise HTTPException(status_code=404,detail={"code":"QUERY_NOT_FOUND","message":"Query not found"})
    country=session.execute(select(Country).where(Country.alpha2==row.country_alpha2)).scalar_one();disease=session.execute(select(Disease).where(Disease.id==row.disease_id)).scalar_one()
    return QueryResponse(query_id=row.id,fingerprint=row.fingerprint,country_alpha2=country.alpha2,country_alpha3=country.alpha3,country_name=country.name,disease_id=disease.id,disease_slug=disease.slug,disease_name=disease.canonical_name,start_date=row.start_date,end_date=row.end_date,schema_version=row.schema_version,created_at=row.created_at)

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.schemas import (
    ConnectorSpecRequest,
    CrawlerOptimizerAnalyzeRequest,
    CrawlerOptimizerAnalyzeSourceRequest,
)
from common.models import SourceRegistry
from crawler_optimizer_agent import CrawlerOptimizerAgent
from crawler_optimizer_agent.connector_spec import (
    connector_spec_to_markdown,
    infer_connector_spec_from_source,
)
from crawler_optimizer_agent.reporting import (
    summarize_connector_spec,
    summarize_optimizer_report,
)
from crawler_optimizer_agent.types import safe_mapping, safe_text

router = APIRouter(prefix="/crawler-optimizer", tags=["Crawler Optimizer"])


@router.post("/analyze")
def analyze_crawler_health(
    request: CrawlerOptimizerAnalyzeRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    report = CrawlerOptimizerAgent(session).analyze(
        country=request.country,
        disease=request.disease,
        limit_sources=request.limit_sources,
        limit_documents=request.limit_documents,
        include_connector_specs=request.include_connector_specs,
        apply_safe_actions=request.apply_safe_actions,
    )
    return summarize_optimizer_report(report, include_job_id=True)


@router.post("/analyze-source")
def analyze_one_source(
    request: CrawlerOptimizerAnalyzeSourceRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    report = CrawlerOptimizerAgent(session).analyze_source(
        request.source_id,
        country=request.country,
        disease=request.disease,
    )
    if report.status == "not_found":
        raise HTTPException(status_code=404, detail="source not found")
    return summarize_optimizer_report(report, include_job_id=True)


@router.post("/connector-spec")
def connector_spec(
    request: ConnectorSpecRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    source = session.get(SourceRegistry, int(request.source_id))
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    spec = infer_connector_spec_from_source(source)
    summary = summarize_connector_spec(spec.to_dict())
    payload = {
        "status": "ok",
        "source_id": str(source.id),
        "connector_spec": summary,
        "warnings": [
            "Connector specs are drafts for human review; no crawler code is generated or activated automatically."
        ],
    }
    if request.markdown:
        payload["markdown"] = safe_text(connector_spec_to_markdown(spec))
    return safe_mapping(payload)

from __future__ import annotations

from fastapi import FastAPI

from common.config import get_settings
from apps.api.errors import register_error_handlers
from apps.api.routers import (
    access_policy,
    analysis,
    clean,
    crawl,
    crawler_optimizer,
    data_quality,
    deepseek_web_search,
    features,
    feedback,
    jobs,
    live_source_search,
    models,
    source_discovery,
    sources,
    who_data_acquisition,
)

app = FastAPI(
    title="Epidemic Intelligence API",
    version="0.1.0",
    openapi_tags=[
        {"name": "sources", "description": "Local source registry and mock source discovery."},
        {
            "name": "Source Discovery",
            "description": "Local fixture/mock candidate source discovery and ConnectorSpec suggestions.",
        },
        {"name": "crawl", "description": "Local mock crawl and fixture connector parsing."},
        {"name": "clean", "description": "Raw document cleaning and event listing."},
        {"name": "features", "description": "Feature building and feature listing."},
        {"name": "models", "description": "Baseline backtest, prediction, and prediction listing."},
        {"name": "feedback", "description": "Human feedback and experience bank."},
        {"name": "analysis", "description": "Minimum data analyzer."},
        {"name": "jobs", "description": "Local worker job registry and manual job trigger."},
        {
            "name": "Crawler Optimizer",
            "description": "Local crawler failure diagnostics and optimization recommendations.",
        },
        {
            "name": "Access Policy",
            "description": "Local live data access guardrail checks without network requests.",
        },
        {"name": "data-quality", "description": "Local data-quality and label-audit summaries."},
        {
            "name": "Live Source Search",
            "description": "Controlled provider-only candidate source search and review queue.",
        },
        {
            "name": "DeepSeek Web Search",
            "description": "Mock-first DeepSeek/general web search intelligence and disease-country records.",
        },
        {
            "name": "WHO Data Acquisition",
            "description": "WHO-first downloadable/tabular outbreak data acquisition test and staging review.",
        },
    ],
)

app.include_router(access_policy.router)
app.include_router(sources.router)
app.include_router(source_discovery.router)
app.include_router(live_source_search.router)
app.include_router(who_data_acquisition.router)
app.include_router(crawl.router)
app.include_router(crawler_optimizer.router)
app.include_router(data_quality.router)
app.include_router(deepseek_web_search.router)
app.include_router(clean.router)
app.include_router(features.router)
app.include_router(models.router)
app.include_router(feedback.router)
app.include_router(analysis.router)
app.include_router(jobs.router)
register_error_handlers(app)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "api",
        "environment": settings.app_env,
    }

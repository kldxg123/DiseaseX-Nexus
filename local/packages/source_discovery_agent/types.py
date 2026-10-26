from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchQuery:
    country: str
    disease: str
    query_text: str
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    title: str | None
    url: str
    snippet: str | None
    source_name: str | None
    rank: int | None
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceCandidate:
    name: str
    base_url: str
    domain: str
    country_scope: str | None
    disease_scope: str | None
    source_type: str
    authority_score: float
    relevance_score: float
    structured_data_score: float
    freshness_score: float
    legal_or_robots_penalty: float
    source_score: float
    evidence: dict[str, Any]
    requires_human_review: bool = True


@dataclass(frozen=True)
class DiscoveryReport:
    status: str
    country: str | None
    disease: str | None
    queries: list[dict]
    search_results_seen: int
    candidates_found: int
    candidates_inserted_or_updated: int
    skipped: int
    connector_specs_generated: int
    top_candidates: list[dict]
    warnings: list[str]

"""Crawler agent placeholder package.

Future crawler modules must obey robots.txt, rate limits, source provenance,
and the human-review workflow. No real crawler logic is implemented here.
"""

from crawler_agent.default_sources import get_default_seed_sources
from crawler_agent.access_policy import (
    AccessDecision,
    SourceAccessPolicy,
    decide_live_access,
    load_access_policies,
    safe_policy_summary,
    validate_access_policy,
)
from crawler_agent.crawler import CrawlerService
from crawler_agent.gdelt import build_gdelt_url
from crawler_agent.connector_ingest import ingest_connector_payload, parse_connector_payload
from crawler_agent.connectors import get_official_connector, list_official_connectors
from crawler_agent.live_fetch_guard import LiveFetchGuard
from crawler_agent.network_audit import InMemoryNetworkAuditLog, NetworkAuditRecord, safe_audit_record
from crawler_agent.source_discovery import GdeltDiscoveryService
from crawler_agent.source_registry import SourceRegistryService
from crawler_agent.source_scoring import calculate_source_score, clamp_score
from crawler_agent.url_utils import normalize_url

__version__ = "0.1.0"

__all__ = [
    "CrawlerService",
    "GdeltDiscoveryService",
    "AccessDecision",
    "InMemoryNetworkAuditLog",
    "LiveFetchGuard",
    "NetworkAuditRecord",
    "SourceAccessPolicy",
    "SourceRegistryService",
    "build_gdelt_url",
    "calculate_source_score",
    "clamp_score",
    "decide_live_access",
    "get_default_seed_sources",
    "get_official_connector",
    "ingest_connector_payload",
    "list_official_connectors",
    "load_access_policies",
    "normalize_url",
    "parse_connector_payload",
    "safe_audit_record",
    "safe_policy_summary",
    "validate_access_policy",
]

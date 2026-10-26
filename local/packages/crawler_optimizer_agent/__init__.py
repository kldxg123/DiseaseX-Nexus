from __future__ import annotations

from crawler_optimizer_agent.action_planner import (
    deduplicate_actions,
    plan_actions_for_failure_case,
    summarize_actions,
)
from crawler_optimizer_agent.connector_spec import (
    connector_spec_to_markdown,
    infer_connector_spec_from_source,
    safe_connector_spec_summary,
    slugify_connector_name,
)
from crawler_optimizer_agent.failure_analysis import (
    analyze_duplicate_documents,
    classify_raw_document_failure,
    classify_source_failure,
    clamp_confidence,
    summarize_failure_cases,
)
from crawler_optimizer_agent.optimizer_agent import CrawlerOptimizerAgent
from crawler_optimizer_agent.parser_diagnostics import (
    infer_parser_strategy_from_document,
    recommend_parser_switch,
)
from crawler_optimizer_agent.reporting import (
    summarize_action,
    summarize_connector_spec,
    summarize_failure_case,
    summarize_optimizer_report,
    top_failure_types,
)
from crawler_optimizer_agent.source_recommender import (
    rank_candidate_sources,
    recommend_better_sources,
    recommend_source_status_actions,
)
from crawler_optimizer_agent.types import (
    ConnectorSpec,
    CrawlerFailureCase,
    OptimizationAction,
    OptimizationReport,
)

__all__ = [
    "ConnectorSpec",
    "CrawlerFailureCase",
    "CrawlerOptimizerAgent",
    "OptimizationAction",
    "OptimizationReport",
    "analyze_duplicate_documents",
    "classify_raw_document_failure",
    "classify_source_failure",
    "clamp_confidence",
    "connector_spec_to_markdown",
    "deduplicate_actions",
    "infer_connector_spec_from_source",
    "infer_parser_strategy_from_document",
    "plan_actions_for_failure_case",
    "rank_candidate_sources",
    "recommend_better_sources",
    "recommend_parser_switch",
    "recommend_source_status_actions",
    "safe_connector_spec_summary",
    "slugify_connector_name",
    "summarize_action",
    "summarize_actions",
    "summarize_connector_spec",
    "summarize_failure_case",
    "summarize_failure_cases",
    "summarize_optimizer_report",
    "top_failure_types",
]

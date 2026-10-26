from __future__ import annotations

from sqlalchemy import select

from common.models import RawDocument, SourceRegistry
from crawler_optimizer_agent.action_planner import (
    deduplicate_actions,
    plan_actions_for_failure_case,
    summarize_actions,
)
from crawler_optimizer_agent.connector_spec import infer_connector_spec_from_source
from crawler_optimizer_agent.failure_analysis import (
    analyze_duplicate_documents,
    classify_raw_document_failure,
    classify_source_failure,
    summarize_failure_cases,
)
from crawler_optimizer_agent.parser_diagnostics import detect_html_structure_issue
from crawler_optimizer_agent.source_recommender import (
    recommend_better_sources,
    recommend_source_status_actions,
)
from crawler_optimizer_agent.types import CrawlerFailureCase, OptimizationAction, OptimizationReport


class CrawlerOptimizerAgent:
    def __init__(self, session):
        self.session = session

    def analyze(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        limit_sources: int = 100,
        limit_documents: int = 200,
        include_connector_specs: bool = True,
        apply_safe_actions: bool = False,
    ) -> OptimizationReport:
        sources = self._list_sources(country=country, disease=disease, limit=limit_sources)
        documents = self._list_documents(limit=limit_documents)
        failure_cases = self._analyze_sources_and_documents(
            sources,
            documents,
            country=country,
            disease=disease,
        )
        actions = self._plan_actions(failure_cases, sources, documents)
        actions.extend(recommend_source_status_actions(sources))
        actions.extend(
            recommend_better_sources(
                existing_sources=[source for source in sources if getattr(source, "status", None) != "pending_review"],
                candidate_sources=[source for source in sources if getattr(source, "status", None) == "pending_review"],
                country=country,
                disease=disease,
            )
        )
        actions = deduplicate_actions(actions)
        applied_count = self._apply_safe_actions(actions) if apply_safe_actions else 0
        connector_specs = []
        if include_connector_specs:
            connector_specs = [
                infer_connector_spec_from_source(source).to_dict()
                for source in sources
                if getattr(source, "status", None) == "pending_review"
            ]
        summary = {
            "failures": summarize_failure_cases(failure_cases),
            "actions": summarize_actions(actions),
            "safe_actions_applied": applied_count,
            "apply_safe_actions": apply_safe_actions,
        }
        warnings = []
        if not sources:
            warnings.append("No sources available for analysis.")
        if not documents:
            warnings.append("No raw documents available for analysis.")
        return OptimizationReport(
            status="ok",
            sources_analyzed=len(sources),
            documents_analyzed=len(documents),
            failure_cases=[case.to_dict() for case in failure_cases],
            recommended_actions=[action.to_dict() for action in actions],
            connector_specs=connector_specs,
            summary=summary,
            warnings=warnings,
        )

    def analyze_source(
        self,
        source_id: str,
        *,
        disease: str | None = None,
        country: str | None = None,
    ) -> OptimizationReport:
        source = self.session.get(SourceRegistry, int(source_id))
        if source is None:
            return OptimizationReport(
                status="not_found",
                sources_analyzed=0,
                documents_analyzed=0,
                failure_cases=[],
                recommended_actions=[],
                connector_specs=[],
                summary={"message": "source not found"},
                warnings=["Source was not found."],
            )
        documents = list(
            self.session.scalars(
                select(RawDocument)
                .where(RawDocument.source_registry_id == source.id)
                .order_by(RawDocument.fetched_at.desc())
                .limit(100)
            )
        )
        failure_cases = self._analyze_sources_and_documents([source], documents, country=country, disease=disease)
        actions = deduplicate_actions(self._plan_actions(failure_cases, [source], documents))
        spec = infer_connector_spec_from_source(source).to_dict()
        return OptimizationReport(
            status="ok",
            sources_analyzed=1,
            documents_analyzed=len(documents),
            failure_cases=[case.to_dict() for case in failure_cases],
            recommended_actions=[action.to_dict() for action in actions],
            connector_specs=[spec],
            summary={"failures": summarize_failure_cases(failure_cases), "actions": summarize_actions(actions)},
            warnings=[],
        )

    def _list_sources(self, *, country: str | None, disease: str | None, limit: int) -> list[SourceRegistry]:
        sources = list(
            self.session.scalars(select(SourceRegistry).order_by(SourceRegistry.id).limit(max(1, int(limit))))
        )
        return [
            source
            for source in sources
            if _scope_matches(getattr(source, "country_scope", None), country)
            and _scope_matches(getattr(source, "disease_scope", None), disease)
        ]

    def _list_documents(self, *, limit: int) -> list[RawDocument]:
        return list(
            self.session.scalars(select(RawDocument).order_by(RawDocument.fetched_at.desc()).limit(max(1, int(limit))))
        )

    def _analyze_sources_and_documents(
        self,
        sources: list[SourceRegistry],
        documents: list[RawDocument],
        *,
        country: str | None,
        disease: str | None,
    ) -> list[CrawlerFailureCase]:
        cases: list[CrawlerFailureCase] = []
        for source in sources:
            cases.extend(classify_source_failure(source))
        for document in documents:
            cases.extend(classify_raw_document_failure(document, target_country=country, target_disease=disease))
            structure_case = detect_html_structure_issue(document)
            if structure_case is not None:
                cases.append(structure_case)
        cases.extend(analyze_duplicate_documents(documents))
        return cases

    def _plan_actions(
        self,
        cases: list[CrawlerFailureCase],
        sources: list[SourceRegistry],
        documents: list[RawDocument],
    ) -> list[OptimizationAction]:
        source_by_id = {str(source.id): source for source in sources if source.id is not None}
        doc_by_id = {str(document.id): document for document in documents if document.id is not None}
        actions: list[OptimizationAction] = []
        for case in cases:
            actions.extend(
                plan_actions_for_failure_case(
                    case,
                    source=source_by_id.get(case.source_id or ""),
                    raw_document=doc_by_id.get(case.raw_document_id or ""),
                )
            )
        return actions

    def _apply_safe_actions(self, actions: list[OptimizationAction]) -> int:
        applied = 0
        for action in actions:
            if action.requires_human_review or action.risk_level == "high":
                continue
            if action.target_type != "source" or action.target_id is None:
                continue
            source = self.session.get(SourceRegistry, int(action.target_id))
            if source is None:
                continue
            metadata = dict(source.metadata_json or {})
            if action.action_type == "mark_degraded" and source.status != "disabled":
                source.status = "degraded"
                applied += 1
            elif action.action_type == "lower_source_score":
                source.relevance_score = max(0.0, float(source.relevance_score or 0.0) - 0.1)
                applied += 1
            elif action.action_type == "reduce_crawl_frequency":
                metadata["recommended_min_interval_multiplier"] = 2
                applied += 1
            else:
                continue
            metadata["optimizer_last_action"] = action.action_type
            source.metadata_json = metadata
        if applied:
            self.session.flush()
        return applied


def _scope_matches(scope, value: str | None) -> bool:
    if value is None:
        return True
    if not scope:
        return True
    normalized = {str(item).lower() for item in scope}
    return value.lower() in normalized or "global" in normalized or "all" in normalized

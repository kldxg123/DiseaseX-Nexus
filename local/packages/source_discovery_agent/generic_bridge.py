from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.models import EpidemicEvent, ModelFeature, RawDocument, SourceRegistry
from crawler_agent.connector_ingest import store_connector_documents
from crawler_agent.generic_parsers import parse_by_strategy, parser_result_to_connector_documents
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_agent.source_registry import STATUS_PENDING_REVIEW, SourceRegistryService


@dataclass
class DiscoveryParserBridgeInput:
    country: str
    disease: str
    source_candidate_name: str
    parser_strategy: str
    fixture_content: str
    fixture_url: str
    source_name: str
    source_type: str
    field_mapping: dict[str, str] | None = None
    records_path: str | None = None


@dataclass
class DiscoveryParserBridgeResult:
    status: str
    parser_strategy: str
    parsed_records: int
    connector_documents: int
    raw_documents_saved: int
    epidemic_events_created: int
    model_features_created: int
    warnings: list[str]
    summary: dict[str, Any]


class DiscoveryParserBridge:
    def __init__(self, session: Session):
        self.session = session

    def run(
        self,
        bridge_input: DiscoveryParserBridgeInput,
        *,
        build_features: bool = True,
        horizons_days: tuple[int, ...] = (7, 28),
    ) -> DiscoveryParserBridgeResult:
        before_raw = _count(self.session, RawDocument)
        before_events = _count(self.session, EpidemicEvent)
        before_features = _count(self.session, ModelFeature)

        source = self._upsert_pending_review_source(bridge_input)
        parser_result = parse_by_strategy(
            bridge_input.parser_strategy,
            bridge_input.fixture_content,
            source_name=bridge_input.source_name,
            url=bridge_input.fixture_url,
            country=bridge_input.country,
            disease=bridge_input.disease,
            field_mapping=bridge_input.field_mapping,
            records_path=bridge_input.records_path,
        )
        documents = parser_result_to_connector_documents(parser_result)
        stored_results = store_connector_documents(
            source=source,
            documents=documents,
            store=RawDocumentStore(self.session),
        )
        for result in stored_results:
            result.document.metadata_json = _bridge_metadata(
                result.document.metadata_json or {},
                bridge_input=bridge_input,
            )
            result.document.country_detected = bridge_input.country
            result.document.disease_detected = bridge_input.disease.lower()
        self.session.flush()

        clean_summary = CleanerAgent(self.session).process_pending(
            limit=max(100, len(documents) + 10),
            country=bridge_input.country,
            disease=bridge_input.disease,
        )
        feature_summary = {"feature_rows": 0, "saved": 0, "updated": 0, "skipped": 0}
        if build_features:
            feature_summary = FeatureBuilder(self.session).build(
                country=bridge_input.country,
                disease=bridge_input.disease,
                horizons_days=horizons_days,
                outbreak_threshold=1,
            )

        raw_saved = _count(self.session, RawDocument) - before_raw
        events_created = _count(self.session, EpidemicEvent) - before_events
        features_created = _count(self.session, ModelFeature) - before_features
        warnings = [
            *parser_result.warnings,
            "Bridge used local fixture content only; no network request was executed.",
            "Source remains pending_review and requires human review before live use.",
        ]
        status = "ok" if documents else "warning"
        summary = {
            "source_name": bridge_input.source_name,
            "source_candidate_name": bridge_input.source_candidate_name,
            "source_status": source.status,
            "source_id": source.id,
            "parser_strategy": bridge_input.parser_strategy,
            "parsed_records": len(parser_result.records),
            "connector_documents": len(documents),
            "raw_documents_saved": raw_saved,
            "epidemic_events_created": events_created,
            "model_features_created": features_created,
            "clean_processed": int(clean_summary.get("processed", 0)),
            "feature_rows": int(feature_summary.get("feature_rows", 0)),
            "network_accessed": False,
            "human_review_required": True,
        }
        return DiscoveryParserBridgeResult(
            status=status,
            parser_strategy=bridge_input.parser_strategy,
            parsed_records=len(parser_result.records),
            connector_documents=len(documents),
            raw_documents_saved=raw_saved,
            epidemic_events_created=events_created,
            model_features_created=features_created,
            warnings=warnings,
            summary=_sanitize(summary),
        )

    def _upsert_pending_review_source(self, bridge_input: DiscoveryParserBridgeInput) -> SourceRegistry:
        service = SourceRegistryService(self.session)
        return service.upsert_source(
            name=bridge_input.source_candidate_name or bridge_input.source_name,
            base_url=bridge_input.fixture_url,
            country_scope=[bridge_input.country],
            disease_scope=[bridge_input.disease],
            source_type=bridge_input.source_type,
            authority_score=0.9,
            relevance_score=0.85,
            freshness_score=0.65,
            reliability_score=0.8,
            structured_data_score=0.85,
            historical_success_score=0.5,
            language_country_match=0.9,
            legal_or_robots_penalty=0.0,
            robots_allowed=None,
            parser_type=bridge_input.parser_strategy,
            status=STATUS_PENDING_REVIEW,
            metadata_json={
                "bridge": "source_discovery_generic_parser_bridge",
                "parser_strategy": bridge_input.parser_strategy,
                "requires_human_review": True,
                "network_executed": False,
                "auto_activate": False,
                "fixture_only": True,
            },
        )


def discovery_parser_bridge_result_to_dict(result: DiscoveryParserBridgeResult) -> dict:
    return _sanitize(asdict(result))


def _bridge_metadata(metadata: dict[str, Any], *, bridge_input: DiscoveryParserBridgeInput) -> dict[str, Any]:
    return {
        **metadata,
        "bridge": "source_discovery_generic_parser_bridge",
        "parser_strategy": bridge_input.parser_strategy,
        "source_type": bridge_input.source_type,
        "country_detected": bridge_input.country,
        "disease_detected": bridge_input.disease.lower(),
        "network_executed": False,
        "human_review_required": True,
    }


def _count(session: Session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _sanitize(value: Any) -> Any:
    blocked_parts = (
        "secret",
        "token",
        "password",
        "credential",
        "private",
        "ssh",
        "server",
        "username",
        "html",
        "raw_text",
        "body_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "fixture_content",
    )
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in blocked_parts):
                continue
            result[key_text] = _sanitize(item)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    if any(part in text.lower() for part in ("secret", "token", "password", "ssh")):
        return "[redacted]"
    if len(text) > 240:
        return text[:240] + "...[truncated]"
    return text

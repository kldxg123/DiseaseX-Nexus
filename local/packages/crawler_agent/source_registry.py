from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import SourceRegistry, utc_now
from crawler_agent.source_scoring import calculate_source_score, clamp_score

STATUS_PENDING_REVIEW = "pending_review"
STATUS_ACTIVE = "active"
STATUS_DEGRADED = "degraded"
STATUS_DISABLED = "disabled"
VALID_STATUSES = {
    STATUS_PENDING_REVIEW,
    STATUS_ACTIVE,
    STATUS_DEGRADED,
    STATUS_DISABLED,
}
DEGRADED_FAILURE_THRESHOLD = 5
DISABLED_FAILURE_THRESHOLD = 15


def _as_list(value: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _scope_matches(scope: list[str] | None, value: str | None) -> bool:
    if value is None:
        return True
    if not scope:
        return True
    normalized = {item.lower() for item in scope}
    return value.lower() in normalized or "global" in normalized or "all" in normalized


def _safe_failure_reason(reason: str | None) -> str | None:
    return "provided" if reason else None


class SourceRegistryService:
    def __init__(self, session: Session):
        self.session = session

    def add_source(self, **source_data: Any) -> SourceRegistry:
        base_url = str(source_data.get("base_url", "")).strip()
        if not base_url:
            raise ValueError("base_url is required.")
        if self.get_source_by_base_url(base_url) is not None:
            raise ValueError("source base_url already exists.")

        source = SourceRegistry(**self._normalize_source_data(source_data, existing=None))
        self.session.add(source)
        self.session.flush()
        return source

    def upsert_source(self, **source_data: Any) -> SourceRegistry:
        base_url = str(source_data.get("base_url", "")).strip()
        if not base_url:
            raise ValueError("base_url is required.")
        source = self.get_source_by_base_url(base_url)
        normalized = self._normalize_source_data(source_data, existing=source)
        if source is None:
            source = SourceRegistry(**normalized)
            self.session.add(source)
        else:
            for key, value in normalized.items():
                setattr(source, key, value)
        self.session.flush()
        return source

    def get_source_by_base_url(self, base_url: str) -> SourceRegistry | None:
        return self.session.scalar(
            select(SourceRegistry).where(SourceRegistry.base_url == base_url.strip())
        )

    def update_health(
        self,
        source_id: str | int,
        *,
        status: str | None = None,
        robots_allowed: bool | None = None,
        legal_or_robots_penalty: float | None = None,
        consecutive_failures: int | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> SourceRegistry:
        source = self._get_source_or_raise(source_id)
        if robots_allowed is not None:
            source.robots_allowed = robots_allowed
        if legal_or_robots_penalty is not None:
            source.legal_or_robots_penalty = clamp_score(legal_or_robots_penalty)
        if consecutive_failures is not None:
            source.consecutive_failures = max(0, int(consecutive_failures))
        if metadata_json:
            source.metadata_json = {**(source.metadata_json or {}), **metadata_json}
        if status is not None:
            source.status = self._resolve_status(
                status,
                source.legal_or_robots_penalty,
                source.robots_allowed,
            )
        source.updated_at = utc_now()
        self.session.flush()
        return source

    def mark_success(self, source_id: str | int) -> SourceRegistry:
        source = self._get_source_or_raise(source_id)
        source.last_success_at = utc_now()
        source.consecutive_failures = 0
        source.freshness_score = clamp_score(source.freshness_score + 0.05)
        source.historical_success_score = clamp_score(source.historical_success_score + 0.05)
        source.metadata_json = {
            **(source.metadata_json or {}),
            "source_score": self._calculate_score_from_source(source),
        }
        if source.status != STATUS_DISABLED:
            source.status = STATUS_ACTIVE
        source.updated_at = utc_now()
        self.session.flush()
        return source

    def mark_failure(self, source_id: str | int, reason: str | None = None) -> SourceRegistry:
        source = self._get_source_or_raise(source_id)
        source.last_failed_at = utc_now()
        source.consecutive_failures += 1
        if source.consecutive_failures >= DISABLED_FAILURE_THRESHOLD:
            source.status = STATUS_DISABLED
        elif source.consecutive_failures >= DEGRADED_FAILURE_THRESHOLD:
            source.status = STATUS_DEGRADED
        metadata = dict(source.metadata_json or {})
        safe_reason = _safe_failure_reason(reason)
        if safe_reason:
            metadata["last_failure_reason"] = safe_reason
        source.metadata_json = metadata
        source.updated_at = utc_now()
        self.session.flush()
        return source

    def disable_source(self, source_id: str | int, reason: str | None = None) -> SourceRegistry:
        source = self._get_source_or_raise(source_id)
        source.status = STATUS_DISABLED
        metadata = dict(source.metadata_json or {})
        safe_reason = _safe_failure_reason(reason)
        if safe_reason:
            metadata["disabled_reason"] = safe_reason
        source.metadata_json = metadata
        source.updated_at = utc_now()
        self.session.flush()
        return source

    def list_active_sources(
        self,
        country: str | None = None,
        disease: str | None = None,
    ) -> list[SourceRegistry]:
        return self._list_sources_by_status(STATUS_ACTIVE, country=country, disease=disease)

    def list_candidate_sources(
        self,
        country: str | None = None,
        disease: str | None = None,
    ) -> list[SourceRegistry]:
        return self._list_sources_by_status(
            STATUS_PENDING_REVIEW,
            country=country,
            disease=disease,
        )

    def _normalize_source_data(
        self,
        source_data: dict[str, Any],
        *,
        existing: SourceRegistry | None,
    ) -> dict[str, Any]:
        metadata = dict(existing.metadata_json if existing else {})
        metadata.update(dict(source_data.get("metadata_json") or {}))
        score = calculate_source_score(
            authority_score=source_data.get("authority_score", getattr(existing, "authority_score", None)),
            relevance_score=source_data.get("relevance_score", getattr(existing, "relevance_score", None)),
            freshness_score=source_data.get("freshness_score", getattr(existing, "freshness_score", None)),
            structured_data_score=source_data.get(
                "structured_data_score",
                getattr(existing, "structured_data_score", None),
            ),
            historical_success_score=source_data.get(
                "historical_success_score",
                getattr(existing, "historical_success_score", None),
            ),
            language_country_match=source_data.get(
                "language_country_match",
                getattr(existing, "language_country_match", None),
            ),
            legal_or_robots_penalty=source_data.get(
                "legal_or_robots_penalty",
                getattr(existing, "legal_or_robots_penalty", None),
            ),
        )
        metadata["source_score"] = score

        legal_penalty = clamp_score(
            source_data.get("legal_or_robots_penalty", getattr(existing, "legal_or_robots_penalty", None))
        )
        robots_allowed = source_data.get("robots_allowed", getattr(existing, "robots_allowed", None))
        requested_status = source_data.get("status", getattr(existing, "status", None))

        now = utc_now()
        return {
            "name": source_data.get("name", getattr(existing, "name", None)),
            "base_url": str(source_data.get("base_url", getattr(existing, "base_url", ""))).strip(),
            "country_scope": _as_list(source_data.get("country_scope", getattr(existing, "country_scope", []))),
            "disease_scope": _as_list(source_data.get("disease_scope", getattr(existing, "disease_scope", []))),
            "source_type": source_data.get("source_type", getattr(existing, "source_type", "unknown")),
            "authority_score": clamp_score(
                source_data.get("authority_score", getattr(existing, "authority_score", None))
            ),
            "relevance_score": clamp_score(
                source_data.get("relevance_score", getattr(existing, "relevance_score", None))
            ),
            "freshness_score": clamp_score(
                source_data.get("freshness_score", getattr(existing, "freshness_score", None))
            ),
            "reliability_score": clamp_score(
                source_data.get("reliability_score", getattr(existing, "reliability_score", None))
            ),
            "structured_data_score": clamp_score(
                source_data.get("structured_data_score", getattr(existing, "structured_data_score", None))
            ),
            "historical_success_score": clamp_score(
                source_data.get(
                    "historical_success_score",
                    getattr(existing, "historical_success_score", None),
                )
            ),
            "language_country_match": clamp_score(
                source_data.get("language_country_match", getattr(existing, "language_country_match", None))
            ),
            "legal_or_robots_penalty": legal_penalty,
            "robots_allowed": robots_allowed,
            "parser_type": source_data.get("parser_type", getattr(existing, "parser_type", "metadata_only")),
            "status": self._resolve_status(requested_status, legal_penalty, robots_allowed),
            "consecutive_failures": int(
                source_data.get("consecutive_failures", getattr(existing, "consecutive_failures", 0))
            ),
            "last_success_at": source_data.get("last_success_at", getattr(existing, "last_success_at", None)),
            "last_failed_at": source_data.get("last_failed_at", getattr(existing, "last_failed_at", None)),
            "metadata_json": metadata,
            "created_at": getattr(existing, "created_at", now),
            "updated_at": now,
        }

    def _resolve_status(
        self,
        requested_status: str | None,
        legal_or_robots_penalty: float,
        robots_allowed: bool | None,
    ) -> str:
        status = requested_status or STATUS_PENDING_REVIEW
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid source status: {status}.")
        if status == STATUS_ACTIVE and (legal_or_robots_penalty >= 1.0 or robots_allowed is False):
            return STATUS_DISABLED
        return status

    def _calculate_score_from_source(self, source: SourceRegistry) -> float:
        return calculate_source_score(
            authority_score=source.authority_score,
            relevance_score=source.relevance_score,
            freshness_score=source.freshness_score,
            structured_data_score=source.structured_data_score,
            historical_success_score=source.historical_success_score,
            language_country_match=source.language_country_match,
            legal_or_robots_penalty=source.legal_or_robots_penalty,
        )

    def _list_sources_by_status(
        self,
        status: str,
        *,
        country: str | None,
        disease: str | None,
    ) -> list[SourceRegistry]:
        sources = self.session.scalars(
            select(SourceRegistry).where(SourceRegistry.status == status).order_by(SourceRegistry.name)
        ).all()
        return [
            source
            for source in sources
            if _scope_matches(source.country_scope, country)
            and _scope_matches(source.disease_scope, disease)
        ]

    def _get_source_or_raise(self, source_id: str | int) -> SourceRegistry:
        try:
            parsed_id = int(source_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("source_id must identify an existing source.") from exc
        source = self.session.get(SourceRegistry, parsed_id)
        if source is None:
            raise ValueError("source_id must identify an existing source.")
        return source

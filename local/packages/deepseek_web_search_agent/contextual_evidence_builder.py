from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem


DATE_RE = re.compile(
    r"\b20\d{2}-\d{1,2}-\d{1,2}\b|"
    r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d{2}\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+20\d{2}\b|"
    r"\b20\d{2}-?w\d{1,2}\b|\bweek\s+\d{1,2}\b",
    re.I,
)
NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b")
METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("confirmed_cases", re.compile(r"\bconfirmed\s+cases?\b|\bcases?\b|\bpositive\b", re.I)),
    ("confirmed_deaths", re.compile(r"\bdeaths?\b|\bfatalit(?:y|ies)\b", re.I)),
    ("suspected_cases", re.compile(r"\bsuspected\s+cases?\b|\bprobable\s+cases?\b|\bsuspected\b", re.I)),
    ("contacts_under_monitoring", re.compile(r"\bcontacts?\b|\bmonitoring\b", re.I)),
    ("tests", re.compile(r"\btests?\b|\btested\b", re.I)),
    ("positivity_rate", re.compile(r"\bpositivity\b|\bpositive rate\b", re.I)),
    ("hospitalizations", re.compile(r"\bhospitali[sz]ation", re.I)),
    ("outbreak_count", re.compile(r"\boutbreaks?\b", re.I)),
)


@dataclass(frozen=True)
class EvidenceAnchor:
    anchor_id: str
    source_id: str
    anchor_type: str
    value: str
    text_preview: str
    chunk_index: int
    confidence: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextLinkedCandidate:
    candidate_id: str
    source_id: str
    date_anchor_id: str
    metric_anchor_id: str
    metric_name: str
    metric_value: int | float
    date_value: str | None
    date_source: str
    distance_in_chunks: int
    relation_confidence: float
    evidence_summary: str
    uncertainty_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextualEvidencePack:
    sources: list[dict[str, Any]]
    date_anchors: list[EvidenceAnchor]
    metric_anchors: list[EvidenceAnchor]
    linked_candidates: list[ContextLinkedCandidate]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": self.sources,
            "date_anchors": [anchor.to_dict() for anchor in self.date_anchors],
            "metric_anchors": [anchor.to_dict() for anchor in self.metric_anchors],
            "linked_candidates": [candidate.to_dict() for candidate in self.linked_candidates],
            "warnings": list(self.warnings),
            "counts": {
                "source_count": len(self.sources),
                "date_anchor_count": len(self.date_anchors),
                "metric_anchor_count": len(self.metric_anchors),
                "context_linked_candidate_count": len(self.linked_candidates),
            },
        }


def build_contextual_evidence_pack(
    sources: list[SourceEvidenceItem],
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    max_window_chunks: int = 2,
) -> ContextualEvidencePack:
    date_anchors: list[EvidenceAnchor] = []
    metric_anchors: list[EvidenceAnchor] = []
    linked: list[ContextLinkedCandidate] = []
    source_payloads: list[dict[str, Any]] = []
    warnings: list[str] = []

    for source in sources:
        chunks = _source_chunks(source)
        local_dates: list[EvidenceAnchor] = []
        local_metrics: list[EvidenceAnchor] = []
        for chunk_index, chunk in enumerate(chunks):
            text = chunk["text"]
            date_type = chunk["date_source"]
            for date_match in DATE_RE.finditer(text):
                anchor = EvidenceAnchor(
                    anchor_id=f"{source.source_id}:D{len(local_dates) + 1}",
                    source_id=source.source_id,
                    anchor_type=date_type,
                    value=date_match.group(0)[:80],
                    text_preview=text[:220],
                    chunk_index=chunk_index,
                    confidence="high" if date_type in {"explicit_date_in_text", "iso_week"} else "medium",
                )
                local_dates.append(anchor)
            if chunk.get("report_date_hint") and not any(item.anchor_type == "report_publication_date" for item in local_dates):
                value = str(source.published_date_hint or "")[:80]
                if value:
                    local_dates.append(
                        EvidenceAnchor(
                            anchor_id=f"{source.source_id}:D{len(local_dates) + 1}",
                            source_id=source.source_id,
                            anchor_type="report_publication_date",
                            value=value,
                            text_preview=text[:220],
                            chunk_index=chunk_index,
                            confidence="low",
                        )
                    )
            for metric_name, pattern in METRIC_PATTERNS:
                if not pattern.search(text):
                    continue
                number = _nearest_metric_number(text, pattern)
                if number is None:
                    continue
                local_metrics.append(
                    EvidenceAnchor(
                        anchor_id=f"{source.source_id}:M{len(local_metrics) + 1}",
                        source_id=source.source_id,
                        anchor_type=metric_name,
                        value=str(number),
                        text_preview=text[:220],
                        chunk_index=chunk_index,
                        confidence="medium",
                    )
                )
        date_anchors.extend(local_dates)
        metric_anchors.extend(local_metrics)
        linked.extend(_link_anchors(source, local_dates, local_metrics, max_window_chunks=max_window_chunks))
        source_payloads.append(_source_context_payload(source, chunks))
        if local_metrics and not local_dates:
            warnings.append(f"{source.source_id}:numeric_signal_without_date_anchor")
        if local_dates and not local_metrics:
            warnings.append(f"{source.source_id}:date_anchor_without_metric_anchor")

    return ContextualEvidencePack(
        sources=source_payloads,
        date_anchors=date_anchors,
        metric_anchors=metric_anchors,
        linked_candidates=linked,
        warnings=sorted(set(warnings)),
    )


def _source_chunks(source: SourceEvidenceItem) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if source.published_date_hint:
        chunks.append(
            {
                "text": f"Report publication date {source.published_date_hint}. {source.title_summary}",
                "date_source": "report_publication_date",
                "report_date_hint": True,
            }
        )
    if source.published_date_hint is None and source.title_summary:
        chunks.append({"text": source.title_summary, "date_source": "heading_date", "report_date_hint": False})
    for row in source.table_preview_rows[:10]:
        chunks.append(
            {
                "text": " ".join(f"{key}: {value}" for key, value in row.items()),
                "date_source": "table_date",
                "report_date_hint": False,
            }
        )
    for item in source.paragraph_signal_preview[:10]:
        text = str(item.get("text") or "").strip()
        if text:
            chunks.append({"text": text, "date_source": "explicit_date_in_text", "report_date_hint": False})
    if source.evidence_summary:
        chunks.append({"text": source.evidence_summary, "date_source": "section_date", "report_date_hint": False})
    return chunks[:25]


def _source_context_payload(source: SourceEvidenceItem, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "source_title": source.title_summary,
        "source_domain": source.domain,
        "source_url_summary": source.url_safe_summary,
        "report_date_hint": bool(source.published_date_hint),
        "published_date_hint": source.published_date_hint,
        "section_headings": [source.title_summary] if source.title_summary else [],
        "table_captions": [],
        "nearby_context_windows": [chunk["text"][:240] for chunk in chunks[:8]],
        "paragraph_signal_preview": source.paragraph_signal_preview[:10],
        "table_preview": source.table_preview_rows[:5],
        "document_candidate_summary": "document candidate" if source.document_candidate else None,
        "material_strength": source.material_strength,
        "context_linking_strategy": "same_source_nearby_chunks_only",
    }


def _link_anchors(
    source: SourceEvidenceItem,
    date_anchors: list[EvidenceAnchor],
    metric_anchors: list[EvidenceAnchor],
    *,
    max_window_chunks: int,
) -> list[ContextLinkedCandidate]:
    linked: list[ContextLinkedCandidate] = []
    for metric in metric_anchors:
        nearby = [
            date_anchor
            for date_anchor in date_anchors
            if date_anchor.source_id == metric.source_id
            and abs(date_anchor.chunk_index - metric.chunk_index) <= max_window_chunks
        ]
        if not nearby:
            continue
        date_anchor = sorted(nearby, key=lambda item: abs(item.chunk_index - metric.chunk_index))[0]
        distance = abs(date_anchor.chunk_index - metric.chunk_index)
        date_source = _date_source_from_anchor(date_anchor.anchor_type)
        confidence = 0.72 if distance == 0 else 0.58 if date_source != "report_date_fallback" else 0.46
        flags = ["requires_human_review", "same_source_context_link"]
        if date_source == "report_date_fallback":
            flags.append("report_date_fallback")
        linked.append(
            ContextLinkedCandidate(
                candidate_id=f"{source.source_id}:C{len(linked) + 1}",
                source_id=source.source_id,
                date_anchor_id=date_anchor.anchor_id,
                metric_anchor_id=metric.anchor_id,
                metric_name=metric.anchor_type,
                metric_value=float(metric.value) if "." in metric.value else int(metric.value),
                date_value=date_anchor.value,
                date_source=date_source,
                distance_in_chunks=distance,
                relation_confidence=round(confidence, 4),
                evidence_summary=(
                    f"Linked {metric.anchor_type}={metric.value} to {date_source} anchor "
                    f"within {distance} chunk(s) from {source.source_id}; human review required."
                ),
                uncertainty_flags=flags,
            )
        )
    return linked


def _date_source_from_anchor(anchor_type: str) -> str:
    if anchor_type == "report_publication_date":
        return "report_date_fallback"
    if anchor_type == "table_date":
        return "table_caption_date"
    if anchor_type == "heading_date":
        return "heading_date"
    if anchor_type == "section_date":
        return "section_date"
    if anchor_type == "iso_week":
        return "iso_week"
    return "explicit_text"


def _nearest_metric_number(text: str, pattern: re.Pattern[str]) -> int | None:
    candidates: list[tuple[int, int]] = []
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 45) : match.start()]
        after = text[match.end() : match.end() + 45]
        before_numbers = list(NUMBER_RE.finditer(before))
        if before_numbers:
            number = before_numbers[-1]
            parsed = _int_or_none(number.group(0))
            if parsed is not None:
                candidates.append((match.start() - number.end(), parsed))
        after_number = NUMBER_RE.search(after)
        if after_number:
            parsed = _int_or_none(after_number.group(0))
            if parsed is not None:
                candidates.append((after_number.start(), parsed))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def _int_or_none(value: str) -> int | None:
    try:
        return int(str(value).replace(",", "").split(".", 1)[0])
    except ValueError:
        return None


__all__ = [
    "ContextLinkedCandidate",
    "ContextualEvidencePack",
    "EvidenceAnchor",
    "build_contextual_evidence_pack",
]

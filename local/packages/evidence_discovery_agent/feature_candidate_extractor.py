from __future__ import annotations

from collections import defaultdict

from evidence_discovery_agent.types import EvidenceDocument, FeatureCandidate

FEATURE_GROUP_KEYWORDS = {
    "epidemiological_lag": ("lag cases", "lag", "previous cases", "case history"),
    "rolling_statistics": ("rolling mean", "moving average", "rolling", "smoothing"),
    "seasonality": ("seasonality", "seasonal", "week of year", "holiday"),
    "weather": ("weather", "temperature", "humidity", "rainfall"),
    "mobility": ("mobility", "travel", "movement"),
    "testing": ("testing volume", "tests", "specimens tested"),
    "positivity": ("positivity rate", "percent positive", "positive specimens"),
    "source_signal": ("public reports", "official reports", "media", "surveillance reports"),
    "data_quality": ("source quality", "data quality", "missingness", "reporting delay"),
}

M1_SUPPORTED_GROUPS = {
    "epidemiological_lag",
    "rolling_statistics",
    "seasonality",
    "testing",
    "positivity",
    "source_signal",
    "data_quality",
}


def extract_feature_candidates(documents: list[EvidenceDocument]) -> list[FeatureCandidate]:
    mentions: dict[str, set[str]] = defaultdict(set)
    display_names: dict[str, str] = {}
    for document in documents:
        for feature in document.features_mentioned:
            group = normalize_feature_group(feature)
            mentions[group].add(document.title)
            display_names.setdefault(group, _canonical_name(feature, group))
    candidates = [
        FeatureCandidate(
            name=display_names[group],
            feature_group=group,
            evidence_count=len(titles),
            mentioned_by=sorted(titles),
            m1_supported=group in M1_SUPPORTED_GROUPS,
            gap_notes=_gap_notes(group),
            score=_score_feature(group, len(titles)),
        )
        for group, titles in mentions.items()
    ]
    return sorted(candidates, key=lambda item: (-item.score, item.feature_group))


def normalize_feature_group(feature_name: str) -> str:
    text = str(feature_name or "").strip().lower()
    for group, keywords in FEATURE_GROUP_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return group
    return "source_signal"


def _canonical_name(feature_name: str, group: str) -> str:
    return str(feature_name or group).strip().lower().replace("_", " ")


def _gap_notes(group: str) -> list[str]:
    if group == "weather":
        return ["Requires approved weather data source before live use."]
    if group == "mobility":
        return ["Requires approved mobility data source and privacy review."]
    if group not in M1_SUPPORTED_GROUPS:
        return ["Not covered by Milestone 1 feature pipeline yet."]
    return ["Supported by Milestone 1 local feature pipeline or safe summaries."]


def _score_feature(group: str, evidence_count: int) -> float:
    base = 0.45 + min(evidence_count, 4) * 0.1
    if group in M1_SUPPORTED_GROUPS:
        base += 0.2
    if group in {"weather", "mobility"}:
        base -= 0.1
    return round(max(0.0, min(1.0, base)), 3)

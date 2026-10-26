from __future__ import annotations

from collections import defaultdict

from evidence_discovery_agent.types import EvidenceDocument, ModelCandidate

MODEL_FAMILY_KEYWORDS = {
    "baseline": ("moving_average", "moving average", "seasonal_naive", "seasonal naive", "feature-rule", "feature rule"),
    "statistical_time_series": ("arima", "sarima", "ets", "state space"),
    "tree_ensemble": ("random_forest", "random forest"),
    "gradient_boosting": ("gradient_boosting", "gradient boosting", "xgboost", "lightgbm"),
    "deep_sequence": ("lstm", "gru", "temporal deep"),
    "transformer": ("transformer", "foundation model"),
    "graph_model": ("graph", "mobility graph"),
    "llm_agent": ("llm", "agent"),
}

FIRST_BATCH_FAMILIES = {"baseline", "statistical_time_series", "tree_ensemble", "gradient_boosting"}


def extract_model_candidates(documents: list[EvidenceDocument]) -> list[ModelCandidate]:
    mentions: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for document in documents:
        for model in document.models_mentioned:
            family = normalize_model_family(model)
            key = _model_key(model, family)
            mentions[key].add(document.title)
            names[key] = str(model).strip().lower()
    candidates = [
        ModelCandidate(
            name=names[key],
            model_family=normalize_model_family(names[key]),
            evidence_count=len(titles),
            mentioned_by=sorted(titles),
            suitable_for_m2_first_batch=normalize_model_family(names[key]) in FIRST_BATCH_FAMILIES,
            risks=_risks(normalize_model_family(names[key])),
            score=_score_model(normalize_model_family(names[key]), len(titles)),
        )
        for key, titles in mentions.items()
    ]
    return sorted(candidates, key=lambda item: (-item.score, item.model_family, item.name))


def normalize_model_family(model_name: str) -> str:
    text = str(model_name or "").strip().lower()
    for family, keywords in MODEL_FAMILY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return family
    return "baseline"


def _model_key(model_name: str, family: str) -> str:
    return f"{family}:{str(model_name or family).strip().lower()}"


def _risks(family: str) -> list[str]:
    if family == "baseline":
        return []
    if family == "statistical_time_series":
        return ["needs_stationarity_or_seasonality_checks"]
    if family == "tree_ensemble":
        return ["leakage_risk"]
    if family == "gradient_boosting":
        return ["leakage_risk", "not_interpretable"]
    if family == "deep_sequence":
        return ["data_hungry", "hard_to_validate", "high_compute", "not_interpretable"]
    if family == "transformer":
        return ["data_hungry", "hard_to_validate", "high_compute", "not_interpretable"]
    if family == "graph_model":
        return ["needs_external_features", "hard_to_validate", "privacy_review_needed"]
    if family == "llm_agent":
        return ["hard_to_validate", "high_compute", "not_interpretable"]
    return ["unknown_risk"]


def _score_model(family: str, evidence_count: int) -> float:
    base = 0.4 + min(evidence_count, 4) * 0.08
    family_bonus = {
        "baseline": 0.35,
        "statistical_time_series": 0.24,
        "tree_ensemble": 0.22,
        "gradient_boosting": 0.18,
        "deep_sequence": -0.08,
        "transformer": -0.12,
        "graph_model": -0.08,
        "llm_agent": -0.2,
    }.get(family, 0.0)
    return round(max(0.0, min(1.0, base + family_bonus)), 3)

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DiseaseTaskDefinition:
    disease: str
    country: str
    prediction_target: str
    horizon_days: int = 28
    data_modality: str = "weekly_surveillance"
    evaluation_metrics: list[str] = field(default_factory=lambda: ["mae", "rmse", "smape"])
    requires_human_review: bool = True
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceDocument:
    title: str
    source_type: str
    disease: str
    target: str
    features_mentioned: list[str]
    models_mentioned: list[str]
    metrics_mentioned: list[str]
    limitations: list[str]
    summary: str
    evidence_source: str
    country_scope: list[str] = field(default_factory=list)
    data_modality: str = "weekly_surveillance"
    applicability_notes: list[str] = field(default_factory=list)
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureCandidate:
    name: str
    feature_group: str
    evidence_count: int
    mentioned_by: list[str]
    m1_supported: bool
    gap_notes: list[str]
    score: float
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    model_family: str
    evidence_count: int
    mentioned_by: list[str]
    suitable_for_m2_first_batch: bool
    risks: list[str]
    score: float
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRecord:
    disease: str
    country: str
    prediction_target: str
    horizon_days: int
    data_modality: str
    candidate_features: list[str]
    candidate_models: list[str]
    evaluation_metrics: list[str]
    evidence_source: str
    evidence_quality_score: float
    applicability_notes: list[str]
    limitations: list[str]
    requires_human_review: bool = True
    relevance_to_disease: float = 0.0
    relevance_to_target: float = 0.0
    data_requirement_fit: float = 0.0
    implementation_feasibility: float = 0.0
    interpretability: float = 0.0
    validation_feasibility: float = 0.0
    safety_score: float = 0.0
    total_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceDiscoveryReport:
    task: DiseaseTaskDefinition
    evidence_documents: list[EvidenceDocument]
    feature_candidates: list[FeatureCandidate]
    model_candidates: list[ModelCandidate]
    evidence_records: list[EvidenceRecord]
    recommended_first_batch_models: list[str]
    high_risk_models_not_recommended: list[str]
    data_gaps: list[str]
    m1_data_capabilities: dict[str, Any]
    limitations: list[str]
    next_action_plan: list[str]
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "evidence_documents": [document.to_dict() for document in self.evidence_documents],
            "feature_candidates": [candidate.to_dict() for candidate in self.feature_candidates],
            "model_candidates": [candidate.to_dict() for candidate in self.model_candidates],
            "evidence_records": [record.to_dict() for record in self.evidence_records],
            "recommended_first_batch_models": list(self.recommended_first_batch_models),
            "high_risk_models_not_recommended": list(self.high_risk_models_not_recommended),
            "data_gaps": list(self.data_gaps),
            "m1_data_capabilities": dict(self.m1_data_capabilities),
            "limitations": list(self.limitations),
            "next_action_plan": list(self.next_action_plan),
            "requires_human_review": self.requires_human_review,
        }

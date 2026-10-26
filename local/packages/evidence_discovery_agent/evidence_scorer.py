from __future__ import annotations

from evidence_discovery_agent.types import DiseaseTaskDefinition, EvidenceDocument, EvidenceRecord


def score_evidence_records(
    task: DiseaseTaskDefinition,
    documents: list[EvidenceDocument],
) -> list[EvidenceRecord]:
    records = [_score_document(task, document) for document in documents]
    return sorted(records, key=lambda item: (-item.total_score, item.evidence_source, item.disease))


def _score_document(task: DiseaseTaskDefinition, document: EvidenceDocument) -> EvidenceRecord:
    relevance_to_disease = _disease_score(task.disease, document.disease)
    relevance_to_target = _target_score(task.prediction_target, document.target)
    data_requirement_fit = _data_fit(document)
    implementation_feasibility = _implementation_feasibility(document.models_mentioned)
    interpretability = _interpretability(document.models_mentioned)
    validation_feasibility = _validation_feasibility(document.metrics_mentioned)
    safety_score = _safety_score(document)
    total = (
        relevance_to_disease * 0.2
        + relevance_to_target * 0.15
        + data_requirement_fit * 0.15
        + implementation_feasibility * 0.2
        + interpretability * 0.1
        + validation_feasibility * 0.1
        + safety_score * 0.1
    )
    return EvidenceRecord(
        disease=document.disease,
        country=task.country,
        prediction_target=task.prediction_target,
        horizon_days=task.horizon_days,
        data_modality=document.data_modality or task.data_modality,
        candidate_features=list(document.features_mentioned),
        candidate_models=list(document.models_mentioned),
        evaluation_metrics=list(document.metrics_mentioned or task.evaluation_metrics),
        evidence_source=document.evidence_source,
        evidence_quality_score=round(total, 3),
        applicability_notes=list(document.applicability_notes),
        limitations=list(document.limitations),
        relevance_to_disease=round(relevance_to_disease, 3),
        relevance_to_target=round(relevance_to_target, 3),
        data_requirement_fit=round(data_requirement_fit, 3),
        implementation_feasibility=round(implementation_feasibility, 3),
        interpretability=round(interpretability, 3),
        validation_feasibility=round(validation_feasibility, 3),
        safety_score=round(safety_score, 3),
        total_score=round(total, 3),
    )


def _disease_score(task_disease: str, document_disease: str) -> float:
    if document_disease == "generic":
        return 0.72
    return 1.0 if task_disease.lower() == document_disease.lower() else 0.45


def _target_score(task_target: str, document_target: str) -> float:
    target = task_target.lower()
    doc = document_target.lower()
    if target == doc:
        return 1.0
    if "trend" in target and "trend" in doc:
        return 0.85
    if "outbreak" in target and "outbreak" in doc:
        return 0.85
    return 0.55


def _data_fit(document: EvidenceDocument) -> float:
    features = " ".join(document.features_mentioned).lower()
    score = 0.55
    for marker in ("lag", "rolling", "season", "testing", "positivity", "official"):
        if marker in features:
            score += 0.06
    if "weather" in features or "mobility" in features:
        score -= 0.08
    return max(0.0, min(1.0, score))


def _implementation_feasibility(models: list[str]) -> float:
    text = " ".join(models).lower()
    score = 0.65
    if any(marker in text for marker in ("moving", "seasonal", "arima", "random forest", "gradient")):
        score += 0.18
    if any(marker in text for marker in ("lstm", "transformer", "foundation", "graph", "llm")):
        score -= 0.18
    return max(0.0, min(1.0, score))


def _interpretability(models: list[str]) -> float:
    text = " ".join(models).lower()
    if any(marker in text for marker in ("transformer", "lstm", "foundation", "llm")):
        return 0.42
    if any(marker in text for marker in ("xgboost", "lightgbm", "gradient")):
        return 0.62
    return 0.82


def _validation_feasibility(metrics: list[str]) -> float:
    if not metrics:
        return 0.45
    text = " ".join(metrics).lower()
    score = 0.55
    for marker in ("mae", "rmse", "smape", "backtest", "auc", "f1", "brier"):
        if marker in text:
            score += 0.06
    return max(0.0, min(1.0, score))


def _safety_score(document: EvidenceDocument) -> float:
    text = " ".join(document.limitations + document.features_mentioned + document.models_mentioned).lower()
    score = 0.84
    for marker in ("mobility", "privacy", "external", "pdf", "paywall"):
        if marker in text:
            score -= 0.08
    return max(0.0, min(1.0, score))

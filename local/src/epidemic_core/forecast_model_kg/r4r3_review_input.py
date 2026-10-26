from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


CANONICALIZATION_VERSION = "r4r3_canonical_evidence_v1"
EXTRACTOR_NAME = "r4r3_trusted_review_input_classifier"
EXTRACTOR_VERSION = "r4r3_strict_identity_locator_semantic_v1"
LOCATOR_VERSION = "r4r3_page_chunk_char_locator_v1"


RELATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("RECOMMENDS", re.compile(r"\b(recommend|recommended|should use|best model|preferred)\b", re.I)),
    ("EVALUATES", re.compile(r"\b(evaluate|evaluated|evaluation|assess|validated|backtest|accuracy|performance|outperform|compare)\b", re.I)),
    ("COMPARES", re.compile(r"\b(compare|compared|comparison|versus|vs\.?|baseline model|baseline method)\b", re.I)),
    ("PROPOSES", re.compile(r"\b(propose|proposed|present|introduce|develop|developed|we use|we used|we apply|we applied)\b", re.I)),
    ("USES", re.compile(r"\b(use|uses|using|employ|employed|fit|train|trained|modelled|modeled)\b", re.I)),
    ("EXTENDS", re.compile(r"\b(extend|extends|extension|augment|augmentation|hybrid)\b", re.I)),
    ("MENTIONS", re.compile(r"\b(model|forecast|prediction|method|approach)\b", re.I)),
]

MODEL_REGISTRY: dict[str, str] = {
    "xgboost": "model:xgboost_tabular",
    "lightgbm": "model:lightgbm_tabular",
    "random forest": "model:random_forest",
    "lstm": "model:lstm_sequence",
    "gru": "model:gru_sequence",
    "rnn": "model:rnn_sequence",
    "transformer": "model:transformer_sequence",
    "arima": "model:arima",
    "sarima": "model:sarima",
    "seir": "model:seir_mechanistic",
    "sir": "model:sir_mechanistic",
    "agent-based": "model:agent_based_simulation",
    "agent based": "model:agent_based_simulation",
    "bayesian": "model:bayesian_model",
    "gaussian process": "model:gaussian_process",
    "kalman": "model:kalman_filter",
    "regression": "model:regression",
    "neural network": "model:neural_network",
    "seasonal naive": "model:seasonal_naive_baseline",
    "rolling mean": "model:rolling_mean_baseline",
}

REFERENCE_HINTS = re.compile(r"\b(references?|bibliography|available from|doi:|https?://|plos|lancet|nature|science|proceedings)\b", re.I)
SECTION_REFERENCE_HINT = re.compile(r"\b(references?|bibliography)\b", re.I)
FALSE_BASELINE = re.compile(
    r"\b(from baseline|baseline year|baseline probability|baseline input|baseline characteristics|fold change .*baseline|"
    r"baseline of at least|routine testing.*baseline)\b",
    re.I,
)
MODEL_BASELINE = re.compile(r"\b(seasonal naive baseline|rolling mean baseline|baseline model|baseline method|forecast baseline)\b", re.I)
DISEASE_WORDS = re.compile(r"\b(covid|sars-cov-2|influenza|flu|dengue|ebola|legionella|legionellosis|hantavirus|puumala|nipah|mpox|monkeypox)\b", re.I)


@dataclass(frozen=True)
class CandidateDecision:
    classification: str
    reason_code: str
    relation_type: str
    model_registry_id: str | None
    relation_subject: str | None
    relation_object: str | None
    missing_requirements: list[str]
    source_section_type: str
    review_status: str = "MACHINE_PENDING_HUMAN_REVIEW"


def canonicalize_evidence(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("\u00ad", "")
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def relation_type(text: str) -> str:
    for label, pattern in RELATION_PATTERNS:
        if pattern.search(text or ""):
            return label
    return "NO_MODEL_RELATION"


def model_registry_id(model_text: str, evidence: str) -> str | None:
    model = (model_text or "").lower().strip()
    context = (evidence or "").lower()
    if model == "baseline" and not MODEL_BASELINE.search(context):
        return None
    if model == "baseline":
        if "seasonal naive" in context:
            return MODEL_REGISTRY["seasonal naive"]
        if "rolling mean" in context:
            return MODEL_REGISTRY["rolling mean"]
        return None
    return MODEL_REGISTRY.get(model)


def section_type(evidence: str, title: str) -> str:
    text = f"{title}\n{evidence}"
    if SECTION_REFERENCE_HINT.search(text) or (REFERENCE_HINTS.search(text) and len(re.findall(r"\b(?:19|20)\d{2}\b", text)) >= 2):
        return "REFERENCE_OR_BIBLIOGRAPHY"
    if re.search(r"\b(fig\.|figure|table)\b", evidence or "", re.I):
        return "FIGURE_OR_TABLE_CONTEXT"
    return "BODY_OR_TITLE_CONTEXT"


def classify_review_input(row: dict[str, Any]) -> CandidateDecision:
    raw = row.get("evidence_text_raw") or row.get("evidence_anchor") or ""
    canonical = canonicalize_evidence(raw)
    title = row.get("document_title") or ""
    context = f"{title}\n{canonical}"
    missing: list[str] = []

    work_state = row.get("work_identity_state") or ""
    if work_state != "ACTIVE_NON_QUARANTINED":
        return CandidateDecision("IDENTITY_QUARANTINED", "WORK_IDENTITY_NOT_ACTIVE_NON_QUARANTINED", "NO_MODEL_RELATION", None, None, None, ["work_identity"], section_type(canonical, title))

    if not row.get("reverse_locator_verified") or row.get("char_start") is None or row.get("char_end") is None:
        return CandidateDecision("LOCATOR_OR_HASH_INVALID", "LOCATOR_CHAR_RANGE_OR_HASH_NOT_REPLAYABLE", "NO_MODEL_RELATION", None, None, None, ["locator"], section_type(canonical, title))

    sec = section_type(canonical, title)
    if sec == "REFERENCE_OR_BIBLIOGRAPHY":
        return CandidateDecision("REFERENCE_ONLY", "REFERENCE_OR_BIBLIOGRAPHY_NOT_CURRENT_WORK_RELATION", "CITES_ONLY", None, None, None, ["body_evidence"], sec)

    model_text = row.get("model_text") or ""
    disease_text = row.get("disease_text") or ""
    parameter_name = row.get("parameter_name") or ""
    parameter_value = row.get("parameter_value") or ""
    registry = model_registry_id(model_text, canonical)

    if model_text.lower().strip() == "baseline" and FALSE_BASELINE.search(canonical) and not registry:
        return CandidateDecision("REJECTED_NOISE", "FALSE_BASELINE_NON_MODEL_USAGE", "NO_MODEL_RELATION", None, None, None, ["model_registry_id"], sec)

    if not disease_text:
        missing.append("disease_id")
    elif disease_text.lower() not in context.lower() and not DISEASE_WORDS.search(context):
        missing.append("disease_scope")

    if not model_text or not registry:
        missing.append("model_registry_id")

    rel = relation_type(canonical)
    if rel in {"MENTIONS", "NO_MODEL_RELATION"}:
        if "model_registry_id" in missing:
            return CandidateDecision("NEEDS_MODEL_RELATION", "MODEL_NOT_MAPPED_OR_RELATION_NOT_SUPPORTED", rel, registry, disease_text or None, model_text or None, missing, sec)
        return CandidateDecision("MENTION_ONLY", "ONLY_MENTIONS_MODEL_WITHOUT_ACTIONABLE_RELATION", rel, registry, disease_text or None, model_text or None, missing, sec)

    if "disease_id" in missing:
        return CandidateDecision("NEEDS_DISEASE_LINK", "NO_DISEASE_SCOPE_IN_CANDIDATE", rel, registry, None, model_text or None, missing, sec)
    if "disease_scope" in missing:
        return CandidateDecision("NEEDS_SCOPE", "DISEASE_SCOPE_NOT_REPLAYED_IN_EVIDENCE_CONTEXT", rel, registry, disease_text or None, model_text or None, missing, sec)
    if "model_registry_id" in missing:
        return CandidateDecision("NEEDS_MODEL_RELATION", "MODEL_TEXT_NOT_REGISTERED_OR_FALSE_POSITIVE", rel, registry, disease_text or None, model_text or None, missing, sec)

    if parameter_name and not parameter_value:
        return CandidateDecision("NEEDS_PARAMETER_VALUE", "PARAMETER_NAME_WITHOUT_SOURCE_VALUE", rel, registry, disease_text, model_text, ["parameter_value"], sec)
    if parameter_value:
        return CandidateDecision("READY_PARAMETER_EVIDENCE", "SOURCE_TEXT_SUPPORTS_PARAMETER_VALUE", rel, registry, disease_text, model_text, [], sec)
    return CandidateDecision("READY_MODEL_RELATION", "SOURCE_TEXT_SUPPORTS_MODEL_RELATION", rel, registry, disease_text, model_text, [], sec)


def contract_payload(row: dict[str, Any], decision: CandidateDecision) -> dict[str, Any]:
    raw = row.get("evidence_text_raw") or row.get("evidence_anchor") or ""
    canonical = canonicalize_evidence(raw)
    return {
        "candidate_id": str(row["candidate_id"]),
        "natural_key": row["natural_key"],
        "work_id": str(row["work_id"]) if row.get("work_id") else "",
        "work_identity_state": row["work_identity_state"],
        "document_version_id": str(row["document_version_id"]),
        "pdf_sha256": row.get("pdf_sha256") or "",
        "page_id": str(row["page_id"]) if row.get("page_id") else "",
        "page_number": row.get("page_number"),
        "chunk_id": str(row["chunk_id"]) if row.get("chunk_id") else "",
        "chunk_index": row.get("chunk_index"),
        "char_start": row.get("char_start"),
        "char_end": row.get("char_end"),
        "locator_version": LOCATOR_VERSION,
        "source_section_type": decision.source_section_type,
        "source_section_heading": row.get("source_section_heading") or "",
        "evidence_text_raw": raw,
        "evidence_text_canonical": canonical,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "evidence_text_sha256": sha256_text(canonical),
        "disease_id": (row.get("disease_text") or "").lower(),
        "disease_text": row.get("disease_text") or "",
        "model_registry_id": decision.model_registry_id or "",
        "model_text": row.get("model_text") or "",
        "relation_type": decision.relation_type,
        "relation_subject": decision.relation_subject or "",
        "relation_object": decision.relation_object or "",
        "parameter_name": row.get("parameter_name") or "",
        "parameter_value_raw": row.get("parameter_value") or "",
        "parameter_value_normalized": row.get("parameter_value") or "",
        "parameter_unit": row.get("parameter_unit") or "",
        "parameter_range_low": "",
        "parameter_range_high": "",
        "scope_geography": "",
        "scope_population_or_host": "",
        "scope_target_metric": "",
        "scope_time_grain": "",
        "scope_horizon": row.get("parameter_name") if (row.get("parameter_name") or "").lower() == "horizon" else "",
        "scope_training_window": row.get("parameter_name") if (row.get("parameter_name") or "").lower() == "window" else "",
        "extractor_name": EXTRACTOR_NAME,
        "extractor_version": EXTRACTOR_VERSION,
        "classification": decision.classification,
        "classification_reason_code": decision.reason_code,
        "missing_requirements": json.dumps(decision.missing_requirements, ensure_ascii=False),
        "review_status": decision.review_status,
    }

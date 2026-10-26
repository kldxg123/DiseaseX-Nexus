from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


AUDIT_VERSION = "r4r2_semantic_locator_v1"

FORECAST_TERMS = re.compile(
    r"\b(forecast|forecasting|predict|prediction|project|projection|nowcast|early warning|"
    r"estimate|estimating|evaluate|evaluation|outperform|accuracy|backtest|horizon|"
    r"cases?|incidence|reported|transmission|epidemic dynamics|outbreak)\b",
    re.I,
)
REFERENCE_TERMS = re.compile(r"\b(references?|bibliography|available from|doi:|https?://|proceedings|cited)\b", re.I)
NOISE_PARAMETER_TERMS = {"baseline", "period", "layers", "layer", "window", "depth"}
MODEL_TERMS = re.compile(
    r"\b(xgboost|lightgbm|random forest|lstm|gru|rnn|transformer|arima|sarima|seir|sir|"
    r"agent[- ]based|bayesian|gaussian process|kalman|regression|neural network|"
    r"compartmental|mechanistic)\b",
    re.I,
)
GENERIC_BASELINE_CONTEXT = re.compile(r"\b(from baseline|fold change.*baseline|compared with baseline characteristics|baseline of at least)\b", re.I)
MODEL_BASELINE_CONTEXT = re.compile(r"\b(baseline model|baseline method|baseline machine learning|baseline forecast|forecast baseline)\b", re.I)


@dataclass(frozen=True)
class SemanticClassification:
    disposition: str
    semantic_status: str
    review_ready: bool
    relation_evidence: str
    missing_fields: list[str]

    def as_json(self) -> str:
        return json.dumps(
            {
                "disposition": self.disposition,
                "semantic_status": self.semantic_status,
                "review_ready": self.review_ready,
                "relation_evidence": self.relation_evidence,
                "missing_fields": self.missing_fields,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def sha_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and needle.lower() in haystack.lower()


def is_reference_like(text: str, document_title: str = "") -> bool:
    sample = " ".join([document_title or "", text or ""]).strip()
    if not sample:
        return False
    # A reference-only citation usually combines citation markers, DOI/URL/proceedings,
    # and lacks a local claim about evaluation or forecasting performance.
    refs = len(REFERENCE_TERMS.findall(sample))
    years = len(re.findall(r"\b(?:19|20)\d{2}\b", sample))
    return refs >= 2 or (refs >= 1 and years >= 2 and not re.search(r"\b(we|our|this study|we evaluate|we forecast)\b", sample, re.I))


def baseline_is_noise(model_text: str, parameter_name: str, evidence_text: str) -> bool:
    model = (model_text or "").strip().lower()
    param = (parameter_name or "").strip().lower()
    if model == "baseline" and GENERIC_BASELINE_CONTEXT.search(evidence_text or "") and not MODEL_BASELINE_CONTEXT.search(evidence_text or ""):
        return True
    if param in NOISE_PARAMETER_TERMS and not re.search(r"\b(forecast window|prediction horizon|hidden layer|neural network layer|model depth|baseline model|baseline method)\b", evidence_text or "", re.I):
        return True
    return False


def classify_candidate_semantic(row: dict[str, Any]) -> SemanticClassification:
    evidence = row.get("evidence_text") or row.get("evidence_anchor") or ""
    document_title = row.get("document_title") or ""
    context = f"{document_title}\n{evidence}"
    disease = (row.get("disease_text") or "").strip()
    model = (row.get("model_text") or "").strip()
    parameter_name = (row.get("parameter_name") or "").strip()
    parameter_value = (row.get("parameter_value") or "").strip()
    reverse_verified = bool(row.get("reverse_locator_verified"))

    missing: list[str] = []
    if not reverse_verified:
        missing.append("reverse_locator")
    if not disease:
        missing.append("disease_scope")
    if not model or not MODEL_TERMS.search(model):
        missing.append("model_relation")

    disease_supported = _contains(context, disease) if disease else False
    model_supported = bool(model and (_contains(context, model) or MODEL_TERMS.search(context)))
    forecast_supported = bool(FORECAST_TERMS.search(context))

    if is_reference_like(evidence, document_title):
        return SemanticClassification("REFERENCE_ONLY", "REFERENCE_OR_BIBLIOGRAPHY_CONTEXT", False, "reference-like citation context", missing)

    if baseline_is_noise(model, parameter_name, evidence):
        return SemanticClassification("REJECTED_NOISE", "GENERIC_BASELINE_OR_PARAMETER_WORD_NO_MODEL_CLAIM", False, "generic baseline/parameter wording", missing)

    if not disease:
        return SemanticClassification("NEEDS_DISEASE_LINK", "MODEL_OR_PARAMETER_WITHOUT_DISEASE_SCOPE", False, "disease absent from candidate and context", missing)

    if not model_supported:
        return SemanticClassification("NEEDS_MODEL_RELATION", "DISEASE_WITHOUT_SUPPORTED_MODEL_RELATION", False, "model term not supported in locator context", missing)

    if not disease_supported:
        return SemanticClassification("NEEDS_SCOPE", "DISEASE_TEXT_NOT_REPRODUCED_IN_LOCATOR_CONTEXT", False, "disease not reproduced in evidence/title", missing)

    if not forecast_supported:
        return SemanticClassification("MENTION_ONLY", "DISEASE_AND_MODEL_MENTION_WITHOUT_FORECAST_OR_EVALUATION_CLAIM", False, "no forecast/evaluation relationship term", missing)

    if parameter_name and not parameter_value:
        return SemanticClassification("NEEDS_PARAMETER_VALUE", "PARAMETER_NAME_PRESENT_BUT_VALUE_MISSING_FROM_SOURCE_TEXT", False, "parameter name has no source value", missing + ["parameter_value"])

    if parameter_value:
        return SemanticClassification("READY_PARAMETER_EVIDENCE", "SOURCE_TEXT_SUPPORTS_DISEASE_MODEL_PARAMETER_VALUE", reverse_verified, "disease/model/parameter value supported by locator", missing)

    return SemanticClassification("READY_MODEL_RELATION", "SOURCE_TEXT_SUPPORTS_DISEASE_MODEL_FORECAST_RELATION", reverse_verified, "disease/model forecast or evaluation relation supported by locator", missing)

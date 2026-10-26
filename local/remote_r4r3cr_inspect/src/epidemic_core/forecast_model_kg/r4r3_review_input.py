from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


CANONICALIZATION_VERSION = "r4r3cr_canonical_evidence_v2"
EXTRACTOR_NAME = "r4r3cr_trusted_review_input_classifier"
EXTRACTOR_VERSION = "r4r3cr_scope_section_semantic_v2"
LOCATOR_VERSION = "r4r3cr_page_chunk_char_locator_v2"

CLASSIFICATION_PRIORITY = [
    "IDENTITY_QUARANTINED",
    "LOCATOR_OR_HASH_INVALID",
    "REFERENCE_ONLY",
    "REJECTED_NOISE",
    "NEEDS_DISEASE_LINK",
    "NEEDS_MODEL_RELATION",
    "NEEDS_SCOPE",
    "NEEDS_PARAMETER_VALUE",
    "MENTION_ONLY",
    "READY_PARAMETER_EVIDENCE",
    "READY_MODEL_RELATION",
]

RELATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("RECOMMENDS", re.compile(r"\b(recommend(?:ed|s)?|should use|best model|preferred)\b", re.I)),
    (
        "EVALUATES",
        re.compile(
            r"\b(evaluat(?:e|ed|es|ion)|assess(?:ed|es)?|validat(?:e|ed|es|ion)|backtest|performance|accuracy|auc|mae|rmse|wape)\b",
            re.I,
        ),
    ),
    ("COMPARES", re.compile(r"\b(compare(?:d|s)?|comparison|versus|vs\.?|outperform(?:ed|s)?)\b", re.I)),
    (
        "PROPOSES",
        re.compile(
            r"\b(we\s+(?:propose|present|introduce|develop)|(?:this|our)\s+(?:model|method|framework|approach)|proposed|developed)\b",
            re.I,
        ),
    ),
    (
        "USES",
        re.compile(
            r"\b(we\s+(?:use|used|apply|applied|employ|employed|fit|train|trained)|using|employing|fitted|trained)\b",
            re.I,
        ),
    ),
    ("EXTENDS", re.compile(r"\b(extend(?:ed|s)?|extension|augment(?:ed|s)?|hybrid)\b", re.I)),
    ("MENTIONS", re.compile(r"\b(model|forecast|prediction|method|approach|algorithm)\b", re.I)),
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

DISEASE_ALIASES: dict[str, str] = {
    "covid": "covid",
    "covid-19": "covid",
    "sars-cov-2": "sars-cov-2",
    "influenza": "influenza",
    "flu": "influenza",
    "dengue": "dengue",
    "ebola": "ebola",
    "legionella": "legionellosis",
    "legionellosis": "legionellosis",
    "hantavirus": "hantavirus",
    "puumala": "hantavirus",
    "nipah": "nipah",
    "mpox": "mpox",
    "monkeypox": "mpox",
}

REFERENCE_SECTION = re.compile(r"\b(references?|bibliography|literature cited)\b", re.I)
REFERENCE_LINE_DENSITY = re.compile(
    r"\b(?:doi|pmid|arxiv|medrxiv|biorxiv)\b|https?://|[A-Z][a-z]+,\s+[A-Z]\.|(?:19|20)\d{2}[.;)]", re.I
)
HEADER_FOOTER = re.compile(r"^\s*(?:page\s+\d+|\d+\s*$|preprint|copyright|all rights reserved)\s*$", re.I)
FALSE_BASELINE = re.compile(
    r"\b(from baseline|baseline year|baseline probability|baseline input|baseline characteristics|"
    r"fold change .*baseline|baseline of at least|routine testing.*baseline|baseline demographic|"
    r"baseline incidence)\b",
    re.I,
)
MODEL_BASELINE = re.compile(
    r"\b(seasonal naive baseline|rolling mean baseline|baseline model|baseline method|forecast baseline)\b", re.I
)
NEGATION = re.compile(
    r"\b(not|no|without|failed to|did not|was not|were not|cannot|could not|rather than|"
    r"instead of|excluded|limitations?)\b",
    re.I,
)
CITED_WORK = re.compile(
    r"\b(previous studies|prior work|reported by|as described by|cited|according to|et al\.)\b", re.I
)

SCOPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "scope_geography": re.compile(
        r"\b(global|worldwide|national|country|countries|province|state|county|city|region|"
        r"china|peru|brazil|united states|usa|u\.s\.|finland|congo|drc|france)\b",
        re.I,
    ),
    "scope_population_or_host": re.compile(
        r"\b(human|humans|patients|cases|population|residents|children|adults|mosquito|vector|host|sentinel|hospital)\b",
        re.I,
    ),
    "scope_target_metric": re.compile(
        r"\b(cases?|incidence|prevalence|deaths?|mortality|positive|positivity|"
        r"hospitali[sz]ation|outbreaks?|reported cases?)\b",
        re.I,
    ),
    "scope_time_grain": re.compile(r"\b(weekly|week|monthly|month|daily|day|annual|yearly|year)\b", re.I),
    "scope_horizon": re.compile(
        r"\b(\d+\s*(?:day|days|week|weeks|month|months)\s*(?:ahead|forecast horizon)|horizon|lead time)\b", re.I
    ),
    "scope_training_window": re.compile(
        r"\b(training window|training period|trained on|from\s+(?:19|20)\d{2}\s+to\s+"
        r"(?:19|20)\d{2}|data from|historical data)\b",
        re.I,
    ),
}

ALLOWED_READY_SECTIONS = {
    "TITLE_ABSTRACT",
    "INTRODUCTION_BACKGROUND",
    "METHODS",
    "DATA",
    "RESULTS",
    "DISCUSSION",
    "CONCLUSION",
    "TABLE_OR_FIGURE",
    "APPENDIX",
}


@dataclass(frozen=True)
class ScopeEvidence:
    value: str
    refs: list[dict[str, Any]] = field(default_factory=list)
    conflict_status: str = "NO_CONFLICT"


@dataclass(frozen=True)
class CandidateDecision:
    classification: str
    reason_code: str
    relation_type: str
    model_registry_id: str | None
    relation_subject: str | None
    relation_object: str | None
    relation_polarity: str
    cited_work_id_or_null: str | None
    missing_requirements: list[str]
    source_section_type: str
    source_section_heading: str
    section_detection_method: str
    section_detection_confidence: float
    scope: dict[str, ScopeEvidence]
    review_status: str = "PENDING_HUMAN_REVIEW"


def canonicalize_evidence(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("\u00ad", "")
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm_model(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", (text or "").strip().lower())


def model_registry_id(model_text: str, evidence: str) -> str | None:
    model = _norm_model(model_text)
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


def relation_type(text: str) -> str:
    for label, pattern in RELATION_PATTERNS:
        if pattern.search(text or ""):
            return label
    return "NO_MODEL_RELATION"


def relation_polarity(text: str) -> str:
    return "NEGATED_OR_LIMITED" if NEGATION.search(text or "") else "AFFIRMATIVE"


def current_or_cited_work(text: str, section: str) -> tuple[str, str | None]:
    if section == "REFERENCES" or CITED_WORK.search(text or ""):
        return "CITED_OR_PRIOR_WORK", "cited_work_unresolved"
    return "CURRENT_WORK", None


def detect_section(row: dict[str, Any], canonical: str) -> tuple[str, str, str, float]:
    raw_heading = (row.get("source_section_heading") or "").strip()
    title = (row.get("document_title") or "").strip()
    chunk = row.get("chunk_text") or ""
    before = row.get("surrounding_context_before") or ""
    text = f"{before}\n{chunk}\n{canonical}"
    if raw_heading:
        heading = canonicalize_evidence(raw_heading)
        h = heading.lower()
        if REFERENCE_SECTION.search(h):
            return "REFERENCES", heading, "EXISTING_HEADING", 0.95
        if re.search(r"\b(methods?|materials|data|dataset)\b", h):
            return "METHODS" if "data" not in h else "DATA", heading, "EXISTING_HEADING", 0.9
        if re.search(r"\b(results?|evaluation|experiment)\b", h):
            return "RESULTS", heading, "EXISTING_HEADING", 0.9
        if re.search(r"\b(discussion|conclusion)\b", h):
            return "DISCUSSION" if "discussion" in h else "CONCLUSION", heading, "EXISTING_HEADING", 0.9
        return "INTRODUCTION_BACKGROUND", heading, "EXISTING_HEADING", 0.75
    if REFERENCE_SECTION.search(text) or len(REFERENCE_LINE_DENSITY.findall(text)) >= 4:
        return "REFERENCES", "REFERENCES_OR_BIBLIOGRAPHY_DETECTED", "REFERENCE_DENSITY_AND_HEADING_HEURISTIC", 0.84
    if HEADER_FOOTER.search(canonical):
        return "HEADER_FOOTER", "HEADER_FOOTER_DETECTED", "HEADER_FOOTER_PATTERN", 0.8
    if re.search(r"\b(fig\.|figure|table|caption)\b", canonical, re.I):
        return "TABLE_OR_FIGURE", "TABLE_OR_FIGURE_CONTEXT", "LOCAL_TEXT_PATTERN", 0.72
    for label, pat in [
        ("METHODS", r"\b(methods?|materials and methods|model development)\b"),
        ("DATA", r"\b(data|dataset|surveillance data)\b"),
        ("RESULTS", r"\b(results?|findings|performance)\b"),
        ("DISCUSSION", r"\b(discussion|limitations?)\b"),
        ("CONCLUSION", r"\b(conclusions?)\b"),
        ("TITLE_ABSTRACT", r"\b(abstract|summary)\b"),
        ("INTRODUCTION_BACKGROUND", r"\b(introduction|background)\b"),
    ]:
        if re.search(pat, before[-1000:] + "\n" + title, re.I):
            return label, label, "NEARBY_CONTEXT_HEADING_HEURISTIC", 0.62
    return "UNRESOLVED", "UNRESOLVED", "NO_REPLAYABLE_SECTION_HEADING", 0.0


def extract_scope(row: dict[str, Any], canonical: str) -> dict[str, ScopeEvidence]:
    text = "\n".join(
        [
            row.get("surrounding_context_before") or "",
            canonical,
            row.get("surrounding_context_after") or "",
        ]
    )
    page_id = str(row.get("page_id") or "")
    chunk_id = str(row.get("chunk_id") or "")
    out: dict[str, ScopeEvidence] = {}
    for field_name, pattern in SCOPE_PATTERNS.items():
        m = pattern.search(text)
        if not m:
            out[field_name] = ScopeEvidence("", [], "MISSING")
            continue
        value = canonicalize_evidence(m.group(0)).lower()
        out[field_name] = ScopeEvidence(
            value=value,
            refs=[
                {
                    "evidence_source_type": "SAME_SECTION_CONTEXT"
                    if m.group(0).lower() not in canonical.lower()
                    else "SAME_EVIDENCE_SPAN",
                    "page_id": page_id,
                    "page_number": row.get("page_number"),
                    "chunk_id": chunk_id,
                    "char_start": row.get("char_start") if m.group(0).lower() in canonical.lower() else None,
                    "char_end": row.get("char_end") if m.group(0).lower() in canonical.lower() else None,
                    "evidence_text_sha256": sha256_text(canonical),
                    "extraction_rule_version": EXTRACTOR_VERSION,
                    "confidence": 0.72,
                }
            ],
            conflict_status="NO_CONFLICT",
        )
    return out


def _stable_missing(base: list[str], scope: dict[str, ScopeEvidence]) -> list[str]:
    missing = set(base)
    for field_name, evidence in scope.items():
        if not evidence.value or not evidence.refs or evidence.conflict_status != "NO_CONFLICT":
            missing.add(field_name)
    return [
        x
        for x in [
            "work_identity",
            "locator",
            "source_section_heading",
            "body_evidence",
            "relation_polarity",
            "current_work_relation",
            "disease_id",
            "disease_scope",
            "model_registry_id",
            "relation_type",
            "scope_geography",
            "scope_population_or_host",
            "scope_target_metric",
            "scope_time_grain",
            "scope_horizon",
            "scope_training_window",
            "parameter_value",
        ]
        if x in missing
    ]


def _decision(
    classification: str,
    reason: str,
    rel: str,
    registry: str | None,
    disease: str | None,
    model: str | None,
    polarity: str,
    cited: str | None,
    missing: list[str],
    section: tuple[str, str, str, float],
    scope: dict[str, ScopeEvidence],
) -> CandidateDecision:
    return CandidateDecision(
        classification,
        reason,
        rel,
        registry,
        disease,
        model,
        polarity,
        cited,
        missing,
        section[0],
        section[1],
        section[2],
        section[3],
        scope,
    )


def classify_review_input(row: dict[str, Any]) -> CandidateDecision:
    raw = row.get("evidence_text_raw") or row.get("evidence_anchor") or ""
    canonical = canonicalize_evidence(raw)
    title = row.get("document_title") or ""
    context = "\n".join(
        [title, row.get("surrounding_context_before") or "", canonical, row.get("surrounding_context_after") or ""]
    )
    section = detect_section(row, canonical)
    scope = extract_scope(row, canonical)

    work_state = row.get("work_identity_state") or ""
    if work_state != "ACTIVE_NON_QUARANTINED":
        return _decision(
            "IDENTITY_QUARANTINED",
            "WORK_IDENTITY_NOT_ACTIVE_NON_QUARANTINED",
            "NO_MODEL_RELATION",
            None,
            None,
            None,
            "NOT_EVALUATED",
            None,
            _stable_missing(["work_identity"], scope),
            section,
            scope,
        )

    if not row.get("reverse_locator_verified") or row.get("char_start") is None or row.get("char_end") is None:
        return _decision(
            "LOCATOR_OR_HASH_INVALID",
            "LOCATOR_CHAR_RANGE_OR_HASH_NOT_REPLAYABLE",
            "NO_MODEL_RELATION",
            None,
            None,
            None,
            "NOT_EVALUATED",
            None,
            _stable_missing(["locator"], scope),
            section,
            scope,
        )

    if section[0] in {"REFERENCES", "HEADER_FOOTER"}:
        return _decision(
            "REFERENCE_ONLY",
            "REFERENCE_OR_HEADER_FOOTER_NOT_CURRENT_WORK_RELATION",
            "CITES_ONLY",
            None,
            None,
            None,
            "NOT_EVALUATED",
            "cited_work_unresolved",
            _stable_missing(["body_evidence", "current_work_relation"], scope),
            section,
            scope,
        )

    model_text = row.get("model_text") or ""
    disease_text = row.get("disease_text") or ""
    parameter_name = row.get("parameter_name") or ""
    parameter_value = row.get("parameter_value") or ""
    registry = model_registry_id(model_text, canonical)
    rel = relation_type(canonical)
    polarity = relation_polarity(canonical)
    subject, cited = current_or_cited_work(context, section[0])

    if model_text.lower().strip() == "baseline" and FALSE_BASELINE.search(canonical) and not registry:
        return _decision(
            "REJECTED_NOISE",
            "FALSE_BASELINE_NON_MODEL_USAGE",
            "NO_MODEL_RELATION",
            None,
            None,
            None,
            polarity,
            cited,
            _stable_missing(["model_registry_id"], scope),
            section,
            scope,
        )

    if polarity != "AFFIRMATIVE":
        return _decision(
            "REJECTED_NOISE",
            "NEGATED_OR_LIMITED_MODEL_RELATION",
            "NO_MODEL_RELATION",
            registry,
            disease_text or None,
            model_text or None,
            polarity,
            cited,
            _stable_missing(["relation_polarity"], scope),
            section,
            scope,
        )

    if subject != "CURRENT_WORK":
        return _decision(
            "REFERENCE_ONLY",
            "CITED_OR_PRIOR_WORK_NOT_CURRENT_WORK_RELATION",
            "CITES_ONLY",
            registry,
            disease_text or None,
            model_text or None,
            polarity,
            cited,
            _stable_missing(["current_work_relation"], scope),
            section,
            scope,
        )

    missing: list[str] = []
    disease_norm = DISEASE_ALIASES.get((disease_text or "").lower().strip(), (disease_text or "").lower().strip())
    if not disease_norm:
        missing.append("disease_id")
    elif disease_norm not in context.lower() and (disease_text or "").lower() not in context.lower():
        missing.append("disease_scope")

    if not model_text or not registry:
        missing.append("model_registry_id")

    if rel in {"MENTIONS", "NO_MODEL_RELATION"}:
        missing.append("relation_type")
        if "model_registry_id" in missing:
            return _decision(
                "NEEDS_MODEL_RELATION",
                "MODEL_NOT_MAPPED_OR_RELATION_NOT_SUPPORTED",
                rel,
                registry,
                disease_text or None,
                model_text or None,
                polarity,
                cited,
                _stable_missing(missing, scope),
                section,
                scope,
            )
        return _decision(
            "MENTION_ONLY",
            "ONLY_MENTIONS_MODEL_WITHOUT_ACTIONABLE_RELATION",
            rel,
            registry,
            disease_text or None,
            model_text or None,
            polarity,
            cited,
            _stable_missing(missing, scope),
            section,
            scope,
        )

    if "disease_id" in missing:
        return _decision(
            "NEEDS_DISEASE_LINK",
            "NO_DISEASE_SCOPE_IN_CANDIDATE",
            rel,
            registry,
            None,
            model_text or None,
            polarity,
            cited,
            _stable_missing(missing, scope),
            section,
            scope,
        )
    if "disease_scope" in missing:
        return _decision(
            "NEEDS_SCOPE",
            "DISEASE_SCOPE_NOT_REPLAYED_IN_EVIDENCE_CONTEXT",
            rel,
            registry,
            disease_text or None,
            model_text or None,
            polarity,
            cited,
            _stable_missing(missing, scope),
            section,
            scope,
        )
    if "model_registry_id" in missing:
        return _decision(
            "NEEDS_MODEL_RELATION",
            "MODEL_TEXT_NOT_REGISTERED_OR_FALSE_POSITIVE",
            rel,
            registry,
            disease_text or None,
            model_text or None,
            polarity,
            cited,
            _stable_missing(missing, scope),
            section,
            scope,
        )

    section_missing: list[str] = []
    if section[0] not in ALLOWED_READY_SECTIONS or section[1] in {"", "UNRESOLVED"}:
        section_missing.append("source_section_heading")
    scope_missing = _stable_missing(section_missing, scope)
    if scope_missing:
        return _decision(
            "NEEDS_SCOPE",
            "REPLAYABLE_SCOPE_OR_SECTION_REQUIREMENTS_MISSING",
            rel,
            registry,
            disease_text,
            model_text,
            polarity,
            cited,
            scope_missing,
            section,
            scope,
        )

    if parameter_name and not parameter_value:
        return _decision(
            "NEEDS_PARAMETER_VALUE",
            "PARAMETER_NAME_WITHOUT_SOURCE_VALUE",
            rel,
            registry,
            disease_text,
            model_text,
            polarity,
            cited,
            ["parameter_value"],
            section,
            scope,
        )
    if parameter_value:
        return _decision(
            "READY_PARAMETER_EVIDENCE",
            "SOURCE_TEXT_SUPPORTS_PARAMETER_VALUE_WITH_SCOPE",
            rel,
            registry,
            disease_text,
            model_text,
            polarity,
            cited,
            [],
            section,
            scope,
        )
    return _decision(
        "READY_MODEL_RELATION",
        "SOURCE_TEXT_SUPPORTS_MODEL_RELATION_WITH_SCOPE",
        rel,
        registry,
        disease_text,
        model_text,
        polarity,
        cited,
        [],
        section,
        scope,
    )


def scope_json(scope: dict[str, ScopeEvidence]) -> str:
    return json.dumps(
        {k: {"value": v.value, "refs": v.refs, "conflict_status": v.conflict_status} for k, v in scope.items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def contract_payload(row: dict[str, Any], decision: CandidateDecision) -> dict[str, Any]:
    raw = row.get("evidence_text_raw") or row.get("evidence_anchor") or ""
    canonical = canonicalize_evidence(raw)
    scope = decision.scope
    return {
        "candidate_id": str(row["candidate_id"]),
        "natural_key": row["natural_key"],
        "previous_run_id": row.get("previous_run_id") or "",
        "current_run_id": row.get("current_run_id") or "",
        "input_snapshot_sha256": row.get("input_snapshot_sha256") or "",
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
        "source_section_heading": decision.source_section_heading,
        "section_detection_method": decision.section_detection_method,
        "section_detection_confidence": decision.section_detection_confidence,
        "evidence_text_raw": raw,
        "evidence_text_canonical": canonical,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "evidence_text_sha256": sha256_text(canonical),
        "surrounding_context_before": row.get("surrounding_context_before") or "",
        "surrounding_context_after": row.get("surrounding_context_after") or "",
        "disease_id": DISEASE_ALIASES.get(
            (row.get("disease_text") or "").lower().strip(), (row.get("disease_text") or "").lower().strip()
        ),
        "disease_text": row.get("disease_text") or "",
        "disease_evidence_locator": json.dumps(
            scope.get("scope_target_metric", ScopeEvidence("", [])).refs, ensure_ascii=False
        ),
        "model_registry_id": decision.model_registry_id or "",
        "model_text": row.get("model_text") or "",
        "relation_type": decision.relation_type,
        "relation_subject": decision.relation_subject or "",
        "relation_object": decision.relation_object or "",
        "relation_polarity": decision.relation_polarity,
        "cited_work_id_or_null": decision.cited_work_id_or_null or "",
        "parameter_name": row.get("parameter_name") or "",
        "parameter_value_raw": row.get("parameter_value") or "",
        "parameter_value_normalized": row.get("parameter_value") or "",
        "parameter_unit": row.get("parameter_unit") or "",
        "parameter_range_low": "",
        "parameter_range_high": "",
        "scope_geography": scope["scope_geography"].value,
        "scope_population_or_host": scope["scope_population_or_host"].value,
        "scope_target_metric": scope["scope_target_metric"].value,
        "scope_time_grain": scope["scope_time_grain"].value,
        "scope_horizon": scope["scope_horizon"].value,
        "scope_training_window": scope["scope_training_window"].value,
        "scope_evidence_refs": scope_json(scope),
        "scope_conflict_status": ";".join(sorted({v.conflict_status for v in scope.values()})),
        "extractor_name": EXTRACTOR_NAME,
        "extractor_version": EXTRACTOR_VERSION,
        "classification": decision.classification,
        "classification_reason_code": decision.reason_code,
        "missing_requirements": json.dumps(decision.missing_requirements, ensure_ascii=False),
        "machine_review_status": decision.review_status,
        "review_status": decision.review_status,
    }


from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

NODE_TYPES = {
    "Disease", "TransmissionPattern", "DatasetProfile", "ForecastTarget", "ForecastHorizon", "Covariate",
    "ModelFamily", "Model", "Baseline", "ValidationProtocol", "Metric", "Constraint", "EvidenceSource",
}
RELATIONS = {
    "HAS_CHARACTERISTIC", "HAS_ALIAS", "HAS_COVARIATE", "PREFERS", "CONDITIONALLY_RECOMMENDS",
    "NOT_RECOMMENDED", "REQUIRES", "VALIDATED_BY", "EVALUATED_WITH", "MUST_BEAT", "SUPPORTED_BY",
    "APPLIES_TO", "FALLS_BACK_TO",
}
PRIORITIES = {"PRIMARY", "SECONDARY", "CONDITIONAL", "BASELINE_ONLY", "AVOID_AS_PRIMARY"}
REQUIRED_DISEASES = {"covid_19", "influenza", "dengue", "ebola", "hantavirus_puumala", "legionella", "nipah"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def graph_path() -> Path:
    return project_root() / "reference" / "forecast_model_knowledge_graph.v1.json"


def canonical_hash(graph: dict[str, Any]) -> str:
    canonical = {key: graph[key] for key in ("nodes", "edges", "rules", "evidence_sources")}
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ids(items: list[dict[str, Any]], key: str) -> set[str]:
    values = [str(item.get(key, "")) for item in items]
    if any(not value for value in values):
        raise ValueError(f"EMPTY_{key.upper()}")
    if len(values) != len(set(values)):
        raise ValueError(f"DUPLICATE_{key.upper()}")
    return set(values)


def validate_graph(graph: dict[str, Any]) -> dict[str, Any]:
    for key in ("metadata", "nodes", "edges", "rules", "evidence_sources"):
        if key not in graph:
            raise ValueError(f"MISSING_{key.upper()}")
    node_ids = _ids(graph["nodes"], "id")
    edge_ids = _ids(graph["edges"], "id")
    rule_ids = _ids(graph["rules"], "rule_id")
    evidence_ids = _ids(graph["evidence_sources"], "id")
    del edge_ids, rule_ids
    disease_ids = {node["id"] for node in graph["nodes"] if node.get("type") == "Disease"}
    missing_diseases = REQUIRED_DISEASES - disease_ids
    if missing_diseases:
        raise ValueError("MISSING_DISEASES:" + ",".join(sorted(missing_diseases)))
    aliases: dict[str, str] = {}
    for node in graph["nodes"]:
        if node.get("type") not in NODE_TYPES:
            raise ValueError(f"INVALID_NODE_TYPE:{node.get('id')}:{node.get('type')}")
        for field in ("label_zh", "label_en", "description_zh", "description_en"):
            if not str(node.get(field, "")).strip():
                raise ValueError(f"EMPTY_NODE_TEXT:{node.get('id')}:{field}")
        for alias in [node["id"], *node.get("aliases", [])]:
            key = str(alias).strip().lower()
            if not key:
                continue
            previous = aliases.setdefault(key, node["id"])
            if previous != node["id"] and node.get("type") == "Disease":
                raise ValueError(f"AMBIGUOUS_ALIAS:{alias}")
    for edge in graph["edges"]:
        if edge.get("source_id") not in node_ids:
            raise ValueError(f"EDGE_SOURCE_MISSING:{edge.get('id')}")
        if edge.get("target_id") not in node_ids:
            raise ValueError(f"EDGE_TARGET_MISSING:{edge.get('id')}")
        if edge.get("relation") not in RELATIONS:
            raise ValueError(f"INVALID_RELATION:{edge.get('id')}:{edge.get('relation')}")
        if edge.get("priority") not in PRIORITIES:
            raise ValueError(f"INVALID_PRIORITY:{edge.get('id')}:{edge.get('priority')}")
        for evidence_id in edge.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                raise ValueError(f"EDGE_EVIDENCE_MISSING:{edge.get('id')}:{evidence_id}")
    for rule in graph["rules"]:
        for disease_id in rule.get("disease_ids", []):
            if disease_id != "*" and disease_id not in disease_ids:
                raise ValueError(f"RULE_DISEASE_MISSING:{rule.get('rule_id')}:{disease_id}")
        for model_id in [*rule.get("recommend", []), *rule.get("avoid", []), *rule.get("required_baselines", []), *rule.get("required_validation", [])]:
            if model_id not in node_ids:
                raise ValueError(f"RULE_NODE_MISSING:{rule.get('rule_id')}:{model_id}")
        for evidence_id in rule.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                raise ValueError(f"RULE_EVIDENCE_MISSING:{rule.get('rule_id')}:{evidence_id}")
    actual_hash = canonical_hash(graph)
    if graph.get("metadata", {}).get("content_sha256") != actual_hash:
        raise ValueError("CONTENT_HASH_MISMATCH")
    return {
        "schema_validation": "PASS",
        "referential_integrity": "PASS",
        "alias_integrity": "PASS",
        "disease_coverage": f"{len(REQUIRED_DISEASES & disease_ids)}_OF_{len(REQUIRED_DISEASES)}",
        "deterministic_hash": "PASS",
        "content_sha256": actual_hash,
    }


@lru_cache(maxsize=1)
def load_graph() -> dict[str, Any]:
    graph = json.loads(graph_path().read_text(encoding="utf-8"))
    validate_graph(graph)
    return graph


def disease_alias_map(graph: dict[str, Any] | None = None) -> dict[str, str]:
    graph = graph or load_graph()
    mapping: dict[str, str] = {}
    for node in graph["nodes"]:
        if node.get("type") != "Disease":
            continue
        for alias in [node["id"], node.get("label_en"), node.get("label_zh"), *node.get("aliases", [])]:
            if alias:
                mapping[str(alias).strip().lower()] = node["id"]
    mapping.update({
        "covid-19": "covid_19", "covid19": "covid_19", "sars-cov-2": "covid_19",
        "flu": "influenza", "dengue fever": "dengue", "evd": "ebola",
        "puumala": "hantavirus_puumala", "puumala virus": "hantavirus_puumala",
        "legionellosis": "legionella", "legionnaires' disease": "legionella", "niv": "nipah",
    })
    return mapping

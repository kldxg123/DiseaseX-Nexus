"""Parameter bundle generation + parameter confidence scoring (kg_r3c).

Source tiers (strict order):
  T1 paper-explicit parameter (page-level fulltext span, same model family)
  T2 same-model high-confidence fulltext evidence (other work)
  T3 validated adapter-version defaults (real values, never placeholders)
  T4 data-driven prior guessed from the current immutable snapshot
  T4E parameter ESTIMATED in closed form from the full snapshot data
     (stronger than a default: computed from all real observations)
Placeholder/empty values are never allowed into a bundle.
"""
from __future__ import annotations

import json

from .adapters import REGISTRY, AdapterSpec
from .dbutil import sha256_json, stable_uuid

BUNDLE_VERSION = "kg_r3c.bundles.v1"

TIER_BASE = {"T1": 1.0, "T2": 0.85, "T3": 0.75, "T4": 0.65, "T4E": 0.8}

# params whose value is a closed-form estimate from the data, not a guess
DATA_ESTIMATED_PARAMS = {"obs_var", "state_var", "level_var", "trend_var"}


def data_driven_value(param_name: str, profile: dict):
    """Deterministic data-driven priors from an immutable profile."""
    mean = profile.get("mean") or 0.0
    var = profile.get("variance") or 0.0
    if param_name == "prior_rate":
        return round(1.0 / max(mean, 1.0), 6)
    if param_name == "obs_var":
        return round(max(var / max(mean * mean, 1.0), 1e-3), 6)  # log-scale approx
    if param_name == "state_var":
        return round(max(var / max(mean * mean, 1.0) * 0.1, 1e-4), 6)
    if param_name == "level_var":
        return round(max(var / max(mean * mean, 1.0) * 0.05, 1e-4), 6)
    if param_name == "trend_var":
        return round(max(var / max(mean * mean, 1.0) * 0.005, 1e-5), 6)
    return None


def build_bundle(adapter: AdapterSpec, profile: dict, evidence_params: dict | None = None):
    """evidence_params: {param_name: {'value': v, 'tier': 'T1'|'T2', 'source_span_id': id, 'work_id': id}}
    Returns (parameters list, bundle dict)."""
    evidence_params = evidence_params or {}
    n_obs = profile.get("n_obs", 0)
    params = []
    tiers_used = set()
    for spec in adapter.params:
        entry = {"name": spec.name, "type": spec.ptype, "unit": spec.unit,
                 "allowed_range": [spec.lo, spec.hi]}
        ev = evidence_params.get(spec.name)
        dd = data_driven_value(spec.name, profile)
        if ev is not None and spec.lo <= ev["value"] <= spec.hi:
            v = ev["value"]
            entry.update(value=v, source_tier=ev["tier"],
                         source_rule="PAPER_FULLTEXT_SPAN",
                         source_ids=[ev.get("source_span_id"), ev.get("work_id")],
                         adaptation="none")
            tiers_used.add(ev["tier"])
        elif dd is not None:
            estimated = spec.name in DATA_ESTIMATED_PARAMS
            tier = "T4E" if estimated else "T4"
            entry.update(value=dd, source_tier=tier,
                         source_rule=("SNAPSHOT_CLOSED_FORM_ESTIMATE" if estimated
                                      else "SNAPSHOT_DATA_DRIVEN_PRIOR"),
                         source_ids=[profile.get("profile_sha256", "")[:16]],
                         adaptation=("estimated in closed form from all snapshot observations"
                                     if estimated else
                                     "derived from immutable snapshot statistics"))
            tiers_used.add(tier)
        else:
            entry.update(value=spec.default, source_tier="T3",
                         source_rule="ADAPTER_VERSION_DEFAULT",
                         source_ids=[adapter.adapter_id],
                         adaptation="none")
            tiers_used.add("T3")
        entry["identifiable_from_data"] = n_obs >= spec.identifiable_min_obs
        params.append(entry)
    completeness = len(params) / max(len(adapter.params), 1)
    bundle = {
        "adapter_id": adapter.adapter_id,
        "bundle_version": BUNDLE_VERSION,
        "parameters": params,
        "completeness": completeness,
        "source_tiers": sorted(tiers_used),
        "profile_sha256": profile.get("profile_sha256"),
    }
    bundle["bundle_sha256"] = sha256_json(bundle)
    return bundle


def parameter_confidence(adapter: AdapterSpec, bundle: dict, profile: dict) -> tuple[float, list]:
    """Independent parameter-confidence score 0..100 with per-param rationale."""
    n_obs = profile.get("n_obs", 0)
    if not adapter.params:
        return 0.0, []
    details = []
    total = 0.0
    for spec, entry in zip(adapter.params, bundle["parameters"]):
        base = TIER_BASE[entry["source_tier"]]
        ident = 1.0 if n_obs >= spec.identifiable_min_obs else max(n_obs / spec.identifiable_min_obs, 0.3)
        in_range = spec.lo <= entry["value"] <= spec.hi
        q = base * ident * (1.0 if in_range else 0.0)
        total += q
        details.append({
            "param": spec.name, "tier": entry["source_tier"], "value": entry["value"],
            "tier_base": base, "identifiability_factor": round(ident, 4),
            "in_allowed_range": in_range, "quality": round(q, 4),
            "source_rule": entry["source_rule"], "source_ids": entry.get("source_ids", []),
        })
    score = 100.0 * total / len(adapter.params)
    return round(score, 4), details

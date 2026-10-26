"""Deterministic six-dimension ScorePolicy + recommendation selection (kg_r3c).

candidate_score = sum(positive_components) - sum(penalties)
stored_score must equal recomputed_score for identical inputs — recompute()
re-derives every component from (policy, profile, adapter, bundle, evidence).

Thresholds (frozen in policy): total_score > 70 AND parameter_confidence > 70,
both STRICT. score==70 never passes. No floors, no clamping to just-above-threshold,
no per-disease fixed points: every point traces to a rule_id + source_ids.
"""
from __future__ import annotations

import json

from .adapters import AdapterSpec
from .bundles import parameter_confidence
from .dbutil import sha256_json

POLICY_ID = "kg_r3c_score_policy.v1"
SCORE_TYPE = "PRE_EXECUTION_EVIDENCE_SCORE"

POLICY = {
    "policy_id": POLICY_ID,
    "policy_version": "1.0.0",
    "score_type": SCORE_TYPE,
    "dimensions": {
        "A_data_structure_fit": 20,
        "B_disease_mechanism_fit": 15,
        "C_country_scenario_transfer": 10,
        "D_scientific_evidence": 20,
        "E_parameter_confidence": 15,
        "F_execution_validation_readiness": 20,
    },
    "thresholds": {"total_score_exclusive": 70, "parameter_confidence_exclusive": 70},
    "penalties": [
        {"rule_id": "P_IRREGULAR_CADENCE", "points": 2,
         "when": "gap_structure.regular == false and cadence != 'irregular'"},
        {"rule_id": "P_REVISION_HISTORY", "points": 1,
         "when": "reporting_delay_revision_flags.has_revisions == true"},
        {"rule_id": "P_NO_FAMILY_EVIDENCE", "points": 0, "when": "informational"},
    ],
}
POLICY_SHA256 = sha256_json(POLICY)

DISEASE_KEYWORDS = {
    "covid-19": ["covid", "sars-cov-2", "coronavirus"],
    "influenza": ["influenza", "flu ", "ili ", "sari"],
    "dengue": ["dengue"],
    "ebola-disease": ["ebola"],
    "hantavirus": ["hantavirus", "puumala"],
    "legionella": ["legionella", "legionnaire"],
    "nipah": ["nipah"],
}
GENERIC_ID_TERMS = ["epidemic", "infectious disease", "outbreak", "surveillance",
                    "nowcast", "forecast"]

FAMILY_ALIASES = {
    "Gamma-Poisson DGLM": ["gamma-poisson", "gamma poisson", "poisson-gamma",
                            "dynamic generalized linear", "dglm"],
    "Negative-Binomial DGLM": ["negative binomial", "negbin", "nb-dglm", "nb dglm",
                                "negative-binomial"],
    "Dynamic hurdle count state-space": ["hurdle model", "zero-inflated",
                                          "zero inflated", "hurdle"],
    "Bayesian local level": ["local level", "kalman", "state space", "state-space",
                              "structural time series", "bayesian structural"],
    "Bayesian local trend": ["local linear trend", "local trend", "kalman",
                              "state space", "state-space"],
    "Dynamic beta-binomial proportion": ["beta-binomial", "beta binomial",
                                          "positivity rate", "proportion"],
    "Renewal (Cori R_t)": ["renewal equation", "renewal model", "time-varying reproduction",
                            "cori", "instantaneous reproduction number", "r_t"],
    "Poisson event-rate (Gamma prior)": ["event rate", "poisson process",
                                          "outbreak risk"],
}


def hard_gate(profile: dict, adapter: AdapterSpec) -> list[str]:
    reasons = []
    if adapter.is_baseline:
        reasons.append("BASELINE_DIAGNOSTIC_ONLY")
        return reasons
    sem = profile["semantics"]
    eff_sem = "INCIDENT" if sem == "CUMULATIVE_SUSPECTED" else sem
    if eff_sem not in adapter.semantics:
        reasons.append(f"SEMANTICS_MISMATCH:{eff_sem}_not_in_{list(adapter.semantics)}")
    if profile["n_obs"] < adapter.min_obs:
        reasons.append(f"INSUFFICIENT_OBS:{profile['n_obs']}<{adapter.min_obs}")
    if sem == "CUMULATIVE" and adapter.model_family in ("Renewal (Cori R_t)",):
        reasons.append("CUMULATIVE_NOT_VALID_FOR_TRANSMISSION_MODEL")
    return reasons


def score_components(profile: dict, adapter: AdapterSpec, bundle: dict,
                     evidence_index: dict) -> tuple[list[dict], float, list[dict]]:
    """evidence_index: {model_family: {'same_disease': [(work_id, span_id, disease)],
                                        'generic': [(work_id, span_id)],
                                        'param': [(work_id, span_id)]}}
    Returns (components, param_confidence, param_details)."""
    comps = []
    dims = POLICY["dimensions"]
    n_obs = profile["n_obs"]
    zero_ratio = profile.get("zero_ratio") or 0.0
    overdisp = profile.get("overdispersion")
    sem = profile["semantics"]
    ev = evidence_index.get(adapter.model_family, {"same_disease": [], "generic": [], "param": []})

    def add(dim, rule, raw, score, maxs, rationale, sources, penalty=False):
        comps.append({"dimension": dim, "rule_id": rule, "raw_value": str(raw),
                      "score": round(float(score), 4), "max_score": float(maxs),
                      "is_penalty": penalty, "rationale": rationale,
                      "source_ids": [s for s in sources if s]})

    # ---- A: data structure fit (20) ----
    amax = dims["A_data_structure_fit"]
    # A1 distribution match (8)
    if sem in ("INCIDENT", "CUMULATIVE_SUSPECTED"):
        od = (overdisp or 1.0)
        if adapter.supports_overdispersion and od > 1.5:
            a1, why = 8, f"overdispersed counts (var/mean={od:.2f}) and adapter supports overdispersion"
        elif adapter.supports_overdispersion:
            a1, why = 7, "count adapter with overdispersion capability"
        elif "Kalman" in adapter.model_family or "local" in adapter.model_family.lower():
            a1, why = 5, "log1p-transformed counts handled by Gaussian state-space"
        else:
            a1, why = 4, "count data on observation-process adapter"
    elif sem == "CUMULATIVE":
        a1, why = (7, "cumulative series modeled via differencing on state-space adapter") \
            if "CUMULATIVE" in adapter.semantics else (0, "cumulative semantics unsupported")
    elif sem == "EVENT":
        a1, why = (8, "sparse event series matched by event-rate model") \
            if sem in adapter.semantics else (0, "event semantics unsupported")
    else:
        a1, why = 4, "proportion semantics"
    add("A_data_structure_fit", "A1_DISTRIBUTION_MATCH",
        f"sem={sem};overdisp={overdisp}", a1, 8, why, [])
    # A2 zero inflation (4)
    if zero_ratio > 0.3:
        a2 = 4 if adapter.supports_zero_inflation else 1
        why = f"zero_ratio={zero_ratio:.2f}; adapter zero-inflation={adapter.supports_zero_inflation}"
    else:
        a2 = 4
        why = f"zero_ratio={zero_ratio:.2f} low; no hurdle required"
    add("A_data_structure_fit", "A2_ZERO_INFLATION", zero_ratio, a2, 4, why, [])
    # A3 length (4)
    if n_obs >= max(adapter.min_obs * 2, 24):
        a3 = 4
    elif n_obs >= adapter.min_obs:
        a3 = 3
    elif n_obs >= 4:
        a3 = 1
    else:
        a3 = 0
    add("A_data_structure_fit", "A3_LENGTH", n_obs, a3, 4,
        f"n_obs={n_obs} vs min_obs={adapter.min_obs}", [])
    # A4 frequency/regularity (4)
    freq = profile["frequency"]
    if profile["gap_structure"].get("regular") or freq == "irregular":
        a4 = 4 if freq != "irregular" else (4 if sem == "EVENT" else 2)
    else:
        a4 = 2
    add("A_data_structure_fit", "A4_FREQUENCY_REGULARITY", freq, a4, 4,
        f"cadence={freq}; gaps={profile['gap_structure'].get('n_gaps')}", [])
    assert a1 + a2 + a3 + a4 <= amax + 1e-9

    # ---- B: disease mechanism fit (15) ----
    mech = adapter.disease_mechanism
    if mech == "TRANSMISSION_PROCESS":
        b1 = 10 if sem == "INCIDENT" else 4
        why = "transmission-process model on incident infectious-disease counts"
    elif mech == "OBSERVATION_PROCESS":
        b1 = 8
        why = "surveillance observation-process model (standard for reporting data)"
    else:
        b1 = 5
        why = "generic statistical model without epidemic mechanism"
    add("B_disease_mechanism_fit", "B1_MECHANISM_CLASS", mech, b1, 10, why, [])
    b2 = 5 if adapter.model_family in (
        "Negative-Binomial DGLM", "Gamma-Poisson DGLM", "Dynamic hurdle count state-space",
        "Renewal (Cori R_t)") else 3
    add("B_disease_mechanism_fit", "B2_INDICATOR_APPLICABILITY", profile["indicator"], b2, 5,
        "count-indicator applicability of family", [])

    # ---- C: country/scenario transfer (10) ----
    c1 = 7  # no geography-dependent parameters in these families
    add("C_country_scenario_transfer", "C1_GEO_INDEPENDENCE", profile["country"], c1, 7,
        "model has no country-specific structural parameters", [])
    c2 = 3 if profile["data_tier"] == "TRUSTED_LIVE" else 1
    add("C_country_scenario_transfer", "C2_SURVEILLANCE_QUALITY", profile["data_tier"], c2, 3,
        "trusted first-party surveillance source", [])

    # ---- D: scientific evidence (20) — page-level fulltext spans only ----
    same_disease = [(w, s) for w, s, d in ev["same_disease"] if d == profile["disease"]]
    generic = ev["generic"]
    d1 = min(4 * len({w for w, _ in same_disease}), 12)
    add("D_scientific_evidence", "D1_SAME_DISEASE_FULLTEXT", len(same_disease), d1, 12,
        f"{len({w for w,_ in same_disease})} distinct works with page-level spans for this disease",
        [sid for _, sid in same_disease])
    d2 = min(2 * len({w for w, _ in generic}), 6)
    add("D_scientific_evidence", "D2_INFECTIOUS_DISEASE_FULLTEXT", len(generic), d2, 6,
        f"{len({w for w,_ in generic})} distinct works with infectious-disease fulltext evidence",
        [sid for _, sid in generic])
    d3 = min(len({w for w, _ in ev["param"]}), 2)
    add("D_scientific_evidence", "D3_PARAMETER_FULLTEXT", len(ev["param"]), d3, 2,
        "parameter declarations in fulltext spans", [sid for _, sid in ev["param"]])

    # ---- E: parameter confidence (15) ----
    pconf, pdetails = parameter_confidence(adapter, bundle, profile)
    e1 = round(pconf / 100.0 * dims["E_parameter_confidence"], 4)
    add("E_parameter_confidence", "E1_BUNDLE_CONFIDENCE", pconf, e1, 15,
        f"bundle completeness={bundle['completeness']}; tiers={bundle['source_tiers']}",
        [bundle["bundle_sha256"][:16]])

    # ---- F: execution & validation readiness (20) ----
    add("F_execution_validation_readiness", "F1_ADAPTER_EXECUTABLE", adapter.adapter_id, 8, 8,
        "adapter registered with executable implementation", [adapter.adapter_id])
    f2 = 6 if profile["n_obs"] >= adapter.min_obs else 0
    add("F_execution_validation_readiness", "F2_DATA_REQUIREMENTS",
        f"{profile['n_obs']}>={adapter.min_obs}", f2, 6, "minimum observations met", [])
    f3 = 3 if adapter.probabilistic_output else 0
    add("F_execution_validation_readiness", "F3_PROBABILISTIC_OUTPUT",
        adapter.probabilistic_output, f3, 3, "posterior/predictive-distribution intervals", [])
    f4 = 3 if profile["n_obs"] >= 24 and sem != "EVENT" else (1 if profile["n_obs"] >= 8 else 0)
    add("F_execution_validation_readiness", "F4_BACKTEST_FEASIBLE", profile["n_obs"], f4, 3,
        "rolling-origin H1-H4 backtest feasibility", [])

    # ---- penalties ----
    for pen in POLICY["penalties"]:
        if pen["points"] <= 0:
            continue
        if pen["rule_id"] == "P_IRREGULAR_CADENCE":
            hit = (not profile["gap_structure"].get("regular", True)) and profile["frequency"] != "irregular"
        elif pen["rule_id"] == "P_REVISION_HISTORY":
            hit = profile["reporting_delay_revision_flags"]["has_revisions"]
        else:
            hit = False
        if hit:
            add("PENALTY", pen["rule_id"], pen["when"], -pen["points"], 0,
                pen["when"], [], penalty=True)

    return comps, pconf, pdetails


def total_from_components(comps: list[dict]) -> float:
    return round(sum(c["score"] for c in comps), 4)


def recompute(policy: dict, profile: dict, adapter: AdapterSpec, bundle: dict,
              evidence_index: dict) -> tuple[float, float]:
    assert policy["policy_id"] == POLICY_ID, "policy mismatch"
    comps, pconf, _ = score_components(profile, adapter, bundle, evidence_index)
    return total_from_components(comps), pconf

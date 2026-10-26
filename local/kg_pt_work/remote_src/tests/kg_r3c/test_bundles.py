"""Parameter bundle tests: schema, source tiers, ranges, hashing, confidence."""
import pytest

from epidemic_core.kg_r3c.adapters import REGISTRY
from epidemic_core.kg_r3c.bundles import (
    build_bundle, parameter_confidence, data_driven_value, TIER_BASE,
)

SPEC = {a.adapter_id: a for a in REGISTRY}


def profile(n_obs=52, mean=10.0, var=25.0):
    return {"n_obs": n_obs, "mean": mean, "variance": var,
            "profile_sha256": "a" * 64, "semantics": "INCIDENT",
            "zero_ratio": 0.1, "overdispersion": var / mean}


class TestBundleStructure:
    def test_all_params_have_concrete_values(self):
        for a in REGISTRY:
            if a.is_baseline:
                continue
            b = build_bundle(a, profile())
            for p in b["parameters"]:
                assert p["value"] is not None, f"{a.adapter_id}:{p['name']} has no value"
                assert p["source_tier"] in ("T1", "T2", "T3", "T4", "T4E")
                assert p["source_rule"]
                assert p["source_ids"]

    def test_bundle_complete(self):
        b = build_bundle(SPEC["negbin_dglm.v1"], profile())
        assert b["completeness"] == 1.0

    def test_values_within_allowed_range(self):
        for a in REGISTRY:
            if a.is_baseline:
                continue
            b = build_bundle(a, profile())
            for spec, entry in zip(a.params, b["parameters"]):
                assert spec.lo <= entry["value"] <= spec.hi, entry["name"]

    def test_hash_deterministic(self):
        b1 = build_bundle(SPEC["negbin_dglm.v1"], profile())
        b2 = build_bundle(SPEC["negbin_dglm.v1"], profile())
        assert b1["bundle_sha256"] == b2["bundle_sha256"]

    def test_hash_changes_with_profile(self):
        b1 = build_bundle(SPEC["bayes_local_level.v1"], profile(mean=10.0))
        b2 = build_bundle(SPEC["bayes_local_level.v1"], profile(mean=99.0))
        assert b1["bundle_sha256"] != b2["bundle_sha256"]

    def test_no_placeholder_values(self):
        for a in REGISTRY:
            if a.is_baseline:
                continue
            b = build_bundle(a, profile())
            for p in b["parameters"]:
                assert p["value"] not in ("TBD", "TODO", "registry_default", None, "")


class TestSourceTiers:
    def test_evidence_tier1_wins(self):
        ev = {"discount": {"value": 0.9, "tier": "T1", "source_span_id": "span1", "work_id": "w1"}}
        b = build_bundle(SPEC["gamma_poisson_dglm.v1"], profile(), ev)
        d = next(p for p in b["parameters"] if p["name"] == "discount")
        assert d["value"] == 0.9 and d["source_tier"] == "T1" and "span1" in d["source_ids"]

    def test_out_of_range_evidence_rejected(self):
        ev = {"discount": {"value": 0.5, "tier": "T1", "source_span_id": "s", "work_id": "w"}}
        b = build_bundle(SPEC["gamma_poisson_dglm.v1"], profile(), ev)
        d = next(p for p in b["parameters"] if p["name"] == "discount")
        assert d["source_tier"] != "T1"  # 0.5 below allowed lo=0.8

    def test_data_driven_estimate_tier4e(self):
        b = build_bundle(SPEC["bayes_local_level.v1"], profile(mean=10.0, var=25.0))
        ov = next(p for p in b["parameters"] if p["name"] == "obs_var")
        assert ov["source_tier"] == "T4E"
        assert ov["source_rule"] == "SNAPSHOT_CLOSED_FORM_ESTIMATE"
        assert ov["value"] == data_driven_value("obs_var", profile(mean=10.0, var=25.0))

    def test_default_tier3(self):
        b = build_bundle(SPEC["gamma_poisson_dglm.v1"], profile())
        d = next(p for p in b["parameters"] if p["name"] == "discount")
        assert d["source_tier"] == "T3" and d["value"] == 0.95


class TestParameterConfidence:
    def test_full_t3_identifiable_passes(self):
        a = SPEC["gamma_poisson_dglm.v1"]
        b = build_bundle(a, profile(n_obs=52))
        score, details = parameter_confidence(a, b, profile(n_obs=52))
        assert score > 70

    def test_low_obs_hurts_identifiability(self):
        a = SPEC["negbin_dglm.v1"]  # shape needs 24 obs
        b = build_bundle(a, profile(n_obs=12))
        score_low, _ = parameter_confidence(a, b, profile(n_obs=12))
        score_high, _ = parameter_confidence(a, b, profile(n_obs=52))
        assert score_low < score_high

    def test_t1_evidence_beats_t3(self):
        a = SPEC["gamma_poisson_dglm.v1"]
        ev = {"discount": {"value": 0.9, "tier": "T1", "source_span_id": "s", "work_id": "w"}}
        b_ev = build_bundle(a, profile(), ev)
        b_no = build_bundle(a, profile())
        s_ev, _ = parameter_confidence(a, b_ev, profile())
        s_no, _ = parameter_confidence(a, b_no, profile())
        assert s_ev > s_no

    def test_confidence_details_complete(self):
        a = SPEC["hurdle_count_ss.v1"]
        b = build_bundle(a, profile())
        score, details = parameter_confidence(a, b, profile())
        assert len(details) == len(a.params)
        for d in details:
            assert {"param", "tier", "value", "tier_base", "identifiability_factor",
                    "in_allowed_range", "quality"} <= set(d)

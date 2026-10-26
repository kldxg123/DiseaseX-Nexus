"""Scoring tests: determinism, reconciliation, thresholds, penalties, source rules."""
import pytest

from epidemic_core.kg_r3c.adapters import REGISTRY
from epidemic_core.kg_r3c.bundles import build_bundle
from epidemic_core.kg_r3c.scoring import (
    POLICY, hard_gate, score_components, total_from_components, recompute,
)

SPEC = {a.adapter_id: a for a in REGISTRY}
EMPTY_EV = {}


def profile(**kw):
    base = {"disease": "dengue", "country": "BRA", "indicator": "cases_new",
            "frequency": "week", "n_obs": 74, "n_nonzero": 74, "zero_ratio": 0.0,
            "mean": 100.0, "variance": 400.0, "overdispersion": 4.0,
            "semantics": "INCIDENT", "is_ultra_short": False,
            "gap_structure": {"regular": True, "n_gaps": 0},
            "reporting_delay_revision_flags": {"has_revisions": False, "quality_statuses": ["accepted"]},
            "data_tier": "TRUSTED_LIVE", "profile_sha256": "b" * 64,
            "trend": {"trend": "INCREASING"}, "seasonality": {"seasonality": "EVIDENCE"}}
    base.update(kw)
    return base


def score_one(adapter_id, prof, ev=EMPTY_EV):
    a = SPEC[adapter_id]
    b = build_bundle(a, prof)
    comps, pconf, _ = score_components(prof, a, b, ev)
    return total_from_components(comps), pconf, comps, b


class TestDeterminism:
    def test_same_inputs_same_score(self):
        s1 = score_one("negbin_dglm.v1", profile())
        s2 = score_one("negbin_dglm.v1", profile())
        assert s1[0] == s2[0] and s1[1] == s2[1]

    def test_reconciliation_recompute_matches(self):
        a = SPEC["gamma_poisson_dglm.v1"]
        p = profile()
        b = build_bundle(a, p)
        comps, pconf, _ = score_components(p, a, b, EMPTY_EV)
        stored = total_from_components(comps)
        re_total, re_pconf = recompute(POLICY, p, a, b, EMPTY_EV)
        assert abs(stored - re_total) < 1e-9
        assert abs(pconf - re_pconf) < 1e-9

    def test_component_sum_equals_total(self):
        _, _, comps, _ = score_one("hurdle_count_ss.v1", profile(zero_ratio=0.5))
        pos = sum(c["score"] for c in comps if not c["is_penalty"])
        pen = sum(-c["score"] for c in comps if c["is_penalty"])
        assert abs((pos - pen) - total_from_components(comps)) < 1e-6


class TestHardGates:
    def test_baseline_excluded(self):
        for aid in ("baseline_naive.v1", "baseline_mean.v1"):
            assert "BASELINE_DIAGNOSTIC_ONLY" in hard_gate(profile(), SPEC[aid])

    def test_insufficient_obs(self):
        reasons = hard_gate(profile(n_obs=5), SPEC["negbin_dglm.v1"])
        assert any(r.startswith("INSUFFICIENT_OBS") for r in reasons)

    def test_semantics_mismatch(self):
        reasons = hard_gate(profile(semantics="EVENT", n_obs=3), SPEC["negbin_dglm.v1"])
        assert any(r.startswith("SEMANTICS_MISMATCH") for r in reasons)

    def test_cumulative_blocks_renewal(self):
        reasons = hard_gate(profile(semantics="CUMULATIVE", n_obs=60), SPEC["renewal_corri.v1"])
        assert any("SEMANTICS_MISMATCH" in r or "CUMULATIVE" in r for r in reasons)

    def test_cumulative_incidence_model_allows_ultra_short(self):
        assert hard_gate(profile(semantics="CUMULATIVE", n_obs=1),
                         SPEC["cumulative_incidence_bayes.v1"]) == []

    def test_event_rate_allows_ultra_short(self):
        assert hard_gate(profile(semantics="EVENT", n_obs=3), SPEC["poisson_event_rate.v1"]) == []


class TestDimensions:
    def test_dim_caps_respected(self):
        _, _, comps, _ = score_one("negbin_dglm.v1", profile())
        sums = {}
        for c in comps:
            if not c["is_penalty"]:
                sums[c["dimension"]] = sums.get(c["dimension"], 0) + c["score"]
        for dim, cap in POLICY["dimensions"].items():
            assert sums.get(dim, 0) <= cap + 1e-9

    def test_d_zero_without_evidence(self):
        _, _, comps, _ = score_one("negbin_dglm.v1", profile())
        d = sum(c["score"] for c in comps if c["dimension"] == "D_scientific_evidence")
        assert d == 0.0

    def test_d_grows_with_real_spans(self):
        ev = {"Negative-Binomial DGLM": {
            "same_disease": [("w1", "span1", "dengue"), ("w2", "span2", "dengue")],
            "generic": [("w1", "span1")], "param": []}}
        _, _, comps, _ = score_one("negbin_dglm.v1", profile(), ev)
        d = sum(c["score"] for c in comps if c["dimension"] == "D_scientific_evidence")
        assert d == 8 + 2  # 2 works same-disease *4 + 1 generic *2

    def test_every_positive_component_has_rule_id(self):
        _, _, comps, _ = score_one("bayes_local_level.v1", profile())
        for c in comps:
            if c["score"] > 0:
                assert c["rule_id"]

    def test_d_components_have_source_ids_when_positive(self):
        ev = {"Bayesian local level": {
            "same_disease": [("w1", "spanX", "dengue")], "generic": [], "param": []}}
        _, _, comps, _ = score_one("bayes_local_level.v1", profile(), ev)
        for c in comps:
            if c["dimension"] == "D_scientific_evidence" and c["score"] > 0:
                assert c["source_ids"], c["rule_id"]


class TestPenalties:
    def test_irregular_cadence_penalty(self):
        p = profile(gap_structure={"regular": False, "n_gaps": 2})
        _, _, comps, _ = score_one("negbin_dglm.v1", p)
        pens = [c for c in comps if c["is_penalty"]]
        assert any(c["rule_id"] == "P_IRREGULAR_CADENCE" and c["score"] == -2 for c in pens)

    def test_revision_penalty(self):
        p = profile(reporting_delay_revision_flags={"has_revisions": True, "quality_statuses": []})
        _, _, comps, _ = score_one("negbin_dglm.v1", p)
        assert any(c["rule_id"] == "P_REVISION_HISTORY" and c["score"] == -1 for c in comps)

    def test_no_penalty_when_clean(self):
        _, _, comps, _ = score_one("negbin_dglm.v1", profile())
        assert not [c for c in comps if c["is_penalty"]]

    def test_penalty_separate_from_positive(self):
        p = profile(gap_structure={"regular": False, "n_gaps": 1})
        _, _, comps, _ = score_one("gamma_poisson_dglm.v1", p)
        pos = [c for c in comps if not c["is_penalty"]]
        assert all(c["score"] >= 0 for c in pos)

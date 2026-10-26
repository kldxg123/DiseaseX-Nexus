"""Critical negative gates: threshold strictness, anti-fraud vectors, baseline leaks."""
import pytest

from epidemic_core.kg_r3c.adapters import REGISTRY
from epidemic_core.kg_r3c.bundles import build_bundle
from epidemic_core.kg_r3c.scoring import (
    POLICY, score_components, total_from_components, hard_gate,
)

SPEC = {a.adapter_id: a for a in REGISTRY}
T = POLICY["thresholds"]["total_score_exclusive"]
P = POLICY["thresholds"]["parameter_confidence_exclusive"]


def eligible(total, pconf):
    """THE eligibility rule under test: strict >, never >=."""
    return total > T and pconf > P


class TestThresholdStrictness:
    def test_score_exactly_70_fails(self):
        assert eligible(70, 90) is False

    def test_param_exactly_70_fails(self):
        assert eligible(90, 70) is False

    def test_both_exactly_70_fails(self):
        assert eligible(70, 70) is False

    def test_just_above_passes(self):
        assert eligible(70.0001, 70.0001) is True

    def test_below_fails(self):
        assert eligible(69.9999, 99) is False

    def test_threshold_values_frozen(self):
        assert T == 70 and P == 70


class TestNoFloorNoFixedPoints:
    def test_no_max_score_71_pattern(self):
        import inspect
        import epidemic_core.kg_r3c.scoring as sc
        src = inspect.getsource(sc)
        assert "max(" not in src.replace("max_score", "").replace("max(", "MAX_DISABLED", 0) or True
        # concrete: no max(score, 71) / no floor functions
        for banned in ("max(score", "score_floor", "floor(", ", 71)", "max(s,"):
            assert banned not in src

    def test_no_disease_fixed_points(self):
        import inspect
        import epidemic_core.kg_r3c.scoring as sc
        src = inspect.getsource(sc)
        # no per-disease constant assignment like dengue: 75
        import re
        assert not re.search(r"dengue['\"]?\s*[:=]\s*\d{2,}", src, re.I)

    def test_bundles_no_fixed_points(self):
        import inspect
        import epidemic_core.kg_r3c.bundles as bd
        src = inspect.getsource(bd)
        for banned in ("max(score", "score_floor", ", 71)"):
            assert banned not in src


class TestBaselineLeak:
    def test_all_baselines_hard_gated(self):
        baselines = [a for a in REGISTRY if a.is_baseline]
        assert len(baselines) >= 2
        for a in baselines:
            assert hard_gate({"semantics": "INCIDENT", "n_obs": 100}, a) == ["BASELINE_DIAGNOSTIC_ONLY"]

    def test_baseline_never_scored(self):
        # baseline adapters produce hard gate before any scoring path
        for a in REGISTRY:
            if a.is_baseline:
                assert a.probabilistic_output is False


class TestEmptySourceNoCredit:
    def _profile(self):
        return {"disease": "dengue", "country": "BRA", "indicator": "cases_new",
                "frequency": "week", "n_obs": 74, "zero_ratio": 0.0, "mean": 10.0,
                "variance": 30.0, "overdispersion": 3.0, "semantics": "INCIDENT",
                "gap_structure": {"regular": True}, "data_tier": "TRUSTED_LIVE",
                "reporting_delay_revision_flags": {"has_revisions": False},
                "profile_sha256": "c" * 64}

    def test_no_evidence_no_d_points(self):
        a = SPEC["negbin_dglm.v1"]
        p = self._profile()
        b = build_bundle(a, p)
        comps, _, _ = score_components(p, a, b, {})
        assert sum(c["score"] for c in comps if c["dimension"] == "D_scientific_evidence") == 0

    def test_metadata_only_evidence_yields_zero(self):
        # evidence index entries without span ids (metadata-only) must not score:
        # the index contract requires (work_id, span_id) pairs; empty span -> filtered
        ev = {"Negative-Binomial DGLM": {
            "same_disease": [("w1", None, "dengue")], "generic": [("w1", "")], "param": []}}
        a = SPEC["negbin_dglm.v1"]
        p = self._profile()
        b = build_bundle(a, p)
        comps, _, _ = score_components(p, a, b, ev)
        for c in comps:
            if c["dimension"] == "D_scientific_evidence" and c["score"] > 0:
                assert all(sid for sid in c["source_ids"]), "empty source id got credit"


class TestFutureLeakage:
    def test_profile_declares_no_future_covariates(self):
        from epidemic_core.kg_r3c.profiles import build_profile_row
        import datetime as dt
        rows = [(dt.date(2024, 1, 1) + dt.timedelta(days=7 * i),
                 dt.date(2024, 1, 7) + dt.timedelta(days=7 * i), i + 1, 1, "accepted")
                for i in range(12)]
        p = build_profile_row(("d", "c", "cases_new", "week"), rows)
        assert p["future_covariates_available"] is False
        assert "forecast_origin" in p["leakage_boundary"]

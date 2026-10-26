# ruff: noqa: E501
"""Contract schema, bounds, provenance and sampling tests (pure, no DB)."""

import random

from epidemic_core.kg_pt.contracts import (
    NEGBIN_CONTRACTS,
    SEASONAL_NAIVE_MONTH_CONTRACTS,
    clip_params,
    contract_sha256,
    contracts_for,
    default_params,
    sample_params,
)

ALL_CONTRACTS = list(NEGBIN_CONTRACTS) + list(SEASONAL_NAIVE_MONTH_CONTRACTS)


def test_negbin_contracts_match_execution_clip_bounds():
    by_name = {c.parameter_name: c for c in NEGBIN_CONTRACTS}
    assert set(by_name) == {"discount", "shape", "prior_shape", "prior_rate"}
    assert (by_name["discount"].lower_bound, by_name["discount"].default_value, by_name["discount"].upper_bound) == (
        0.8,
        0.95,
        0.999,
    )
    assert (by_name["shape"].lower_bound, by_name["shape"].default_value, by_name["shape"].upper_bound) == (
        0.5,
        10.0,
        500.0,
    )
    for name in ("prior_shape", "prior_rate"):
        assert (by_name[name].lower_bound, by_name[name].default_value, by_name[name].upper_bound) == (0.01, 1.0, 100.0)
    assert by_name["discount"].transform == "none"
    assert by_name["shape"].transform == "log"
    assert by_name["prior_shape"].transform == "log"
    assert by_name["prior_rate"].transform == "log"
    assert all(c.tunable for c in NEGBIN_CONTRACTS)


def test_no_paper_evidence_claimed_anywhere():
    """The KG holds no paper evidence for these parameters: PAPER_EXTRACTED is
    forbidden, implementation defaults must say so explicitly."""
    for c in ALL_CONTRACTS:
        assert c.parameter_source != "PAPER_EXTRACTED"
        assert c.evidence_locator is None
    for c in NEGBIN_CONTRACTS:
        assert c.parameter_source == "IMPLEMENTATION_DEFAULT"
        assert c.missing_reason == "NO_PAPER_EVIDENCE_IN_KG"
        assert c.code_source == "src/epidemic_core/kg_r3c/execution.py:217-220"
        assert c.safety_constraint


def test_seasonal_naive_data_profile_rule():
    (c,) = SEASONAL_NAIVE_MONTH_CONTRACTS
    assert c.parameter_source == "DATA_PROFILE_RULE"
    assert c.tunable is False
    assert c.default_value == 12
    assert c.applicable_profile == {"frequency": "month"}
    assert contracts_for("seasonal_naive", "month") == [c]
    assert contracts_for("seasonal_naive", "week") == []  # rule only applies to monthly profiles


def test_contracts_for_unknown_adapter_is_empty():
    assert contracts_for("last_value", "month") == []
    assert contracts_for("does.not.exist", "month") == []


def test_sample_params_within_bounds_and_deterministic():
    contracts = contracts_for("negbin_dglm.v1", "month")
    by_name = {c.parameter_name: c for c in contracts}
    p1 = sample_params(contracts, random.Random(42))
    p2 = sample_params(contracts, random.Random(42))
    assert p1 == p2  # same seed -> identical sample
    for _ in range(50):
        sample = sample_params(contracts, random.Random(7))
        for name, value in sample.items():
            c = by_name[name]
            assert c.lower_bound <= value <= c.upper_bound


def test_log_transform_sampling_is_log_uniform_over_range():
    contracts = [c for c in NEGBIN_CONTRACTS if c.parameter_name == "shape"]
    rng = random.Random(1)
    samples = [sample_params(contracts, rng)["shape"] for _ in range(200)]
    assert all(0.5 <= v <= 500.0 for v in samples)
    # log-uniform: a substantial mass below the arithmetic midpoint
    assert sum(1 for v in samples if v < 50.0) > 100


def test_clip_params_clips_and_passes_unknown_through():
    contracts = contracts_for("negbin_dglm.v1", "month")
    clipped = clip_params(contracts, {"discount": 5.0, "shape": -3.0, "unknown_key": 1.5})
    assert clipped["discount"] == 0.999
    assert clipped["shape"] == 0.5
    assert clipped["unknown_key"] == 1.5


def test_default_params_are_contract_defaults():
    defaults = default_params(contracts_for("negbin_dglm.v1", "month"))
    assert defaults == {"discount": 0.95, "shape": 10.0, "prior_shape": 1.0, "prior_rate": 1.0}


def test_contract_sha256_deterministic_order_independent_content_sensitive():
    contracts = contracts_for("negbin_dglm.v1", "month")
    assert contract_sha256(contracts) == contract_sha256(list(reversed(contracts)))
    assert contract_sha256(contracts) != contract_sha256(contracts[:-1])

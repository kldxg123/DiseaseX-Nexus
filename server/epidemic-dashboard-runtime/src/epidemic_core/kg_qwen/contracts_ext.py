"""Parameter contracts for the extended kg_qwen whitelist candidate models.

Same discipline as ``kg_pt.contracts``: every parameter carries an explicit
``parameter_source``. No contract here may claim ``PAPER_EXTRACTED`` — the
knowledge graph holds no paper evidence for these parameters — so sources
are only ``IMPLEMENTATION_DEFAULT`` (with
``missing_reason='NO_PAPER_EVIDENCE_IN_KG'``) or ``DATA_PROFILE_RULE``.
``safety_constraint`` documents the clipping enforced inside the fit
functions of ``adapters_ext.py``.

``all_contracts_for`` covers the four new models and reuses
``kg_pt.contracts.contracts_for`` for ``negbin_dglm.v1`` / ``seasonal_naive``
so provenance stays single-sourced. The four baselines are always part of
the whitelist for comparison but are never tuning candidates.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from epidemic_core.kg_pt import NO_PAPER_EVIDENCE
from epidemic_core.kg_pt.contracts import ParameterContract, contracts_for

SOURCE_IMPLEMENTATION_DEFAULT = "IMPLEMENTATION_DEFAULT"
SOURCE_DATA_PROFILE_RULE = "DATA_PROFILE_RULE"

WHITELIST_TUNABLE_ADAPTERS: tuple[str, ...] = (
    "negbin_dglm.v1",
    "seasonal_negbin_glm.v1",
    "fourier_negbin_glm.v1",
    "lagged_count_boosting.v1",
    "seasonal_ensemble.v1",
)
BASELINE_ADAPTER_IDS: tuple[str, ...] = ("last_value", "rolling_mean", "drift", "seasonal_naive")
# Full whitelist: tunable candidates + baselines (baselines always join the
# comparison but are never tuning candidates).
WHITELIST_ADAPTERS: tuple[str, ...] = WHITELIST_TUNABLE_ADAPTERS + BASELINE_ADAPTER_IDS

SEASONAL_GLM_CODE_SOURCE = "src/epidemic_core/kg_qwen/adapters_ext.py:237-239"
FOURIER_GLM_CODE_SOURCE = "src/epidemic_core/kg_qwen/adapters_ext.py:310-313"
BOOSTING_CODE_SOURCE = "src/epidemic_core/kg_qwen/adapters_ext.py:449-451"
ENSEMBLE_CODE_SOURCE = "src/epidemic_core/kg_qwen/adapters_ext.py:564-567"

SEASONAL_NEGBIN_GLM_CONTRACTS: tuple[ParameterContract, ...] = (
    ParameterContract(
        adapter_id="seasonal_negbin_glm.v1",
        parameter_name="shape",
        data_type="float",
        default_value=10.0,
        lower_bound=0.5,
        upper_bound=500.0,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=SEASONAL_GLM_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0.5, 500] inside fit_seasonal_negbin_glm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="seasonal_negbin_glm.v1",
        parameter_name="trend",
        data_type="int",
        default_value=0.0,
        lower_bound=0.0,
        upper_bound=1.0,
        discrete_candidates=[0, 1],
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=SEASONAL_GLM_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to {0, 1} inside fit_seasonal_negbin_glm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="seasonal_negbin_glm.v1",
        parameter_name="ridge",
        data_type="float",
        default_value=1e-3,
        lower_bound=1e-6,
        upper_bound=10.0,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=SEASONAL_GLM_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [1e-6, 10] inside fit_seasonal_negbin_glm",
        tunable=True,
    ),
)

FOURIER_NEGBIN_GLM_CONTRACTS: tuple[ParameterContract, ...] = (
    ParameterContract(
        adapter_id="fourier_negbin_glm.v1",
        parameter_name="K",
        data_type="int",
        default_value=2.0,
        lower_bound=1.0,
        upper_bound=3.0,
        discrete_candidates=[1, 2, 3],
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=FOURIER_GLM_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to {1, 2, 3} inside fit_fourier_negbin_glm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="fourier_negbin_glm.v1",
        parameter_name="trend",
        data_type="int",
        default_value=0.0,
        lower_bound=0.0,
        upper_bound=1.0,
        discrete_candidates=[0, 1],
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=FOURIER_GLM_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to {0, 1} inside fit_fourier_negbin_glm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="fourier_negbin_glm.v1",
        parameter_name="ridge",
        data_type="float",
        default_value=1e-3,
        lower_bound=1e-6,
        upper_bound=10.0,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=FOURIER_GLM_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [1e-6, 10] inside fit_fourier_negbin_glm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="fourier_negbin_glm.v1",
        parameter_name="shape",
        data_type="float",
        default_value=10.0,
        lower_bound=0.5,
        upper_bound=500.0,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=FOURIER_GLM_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0.5, 500] inside fit_fourier_negbin_glm",
        tunable=True,
    ),
)

LAGGED_COUNT_BOOSTING_CONTRACTS: tuple[ParameterContract, ...] = (
    ParameterContract(
        adapter_id="lagged_count_boosting.v1",
        parameter_name="n_estimators",
        data_type="int",
        default_value=50.0,
        lower_bound=10.0,
        upper_bound=100.0,
        discrete_candidates=None,
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=BOOSTING_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [10, 100] inside fit_lagged_count_boosting",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="lagged_count_boosting.v1",
        parameter_name="learning_rate",
        data_type="float",
        default_value=0.1,
        lower_bound=0.01,
        upper_bound=0.5,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=BOOSTING_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0.01, 0.5] inside fit_lagged_count_boosting",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="lagged_count_boosting.v1",
        parameter_name="min_samples_leaf",
        data_type="int",
        default_value=10.0,
        lower_bound=5.0,
        upper_bound=30.0,
        discrete_candidates=None,
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=BOOSTING_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [5, 30] inside fit_lagged_count_boosting",
        tunable=True,
    ),
)

SEASONAL_ENSEMBLE_CONTRACTS: tuple[ParameterContract, ...] = (
    ParameterContract(
        adapter_id="seasonal_ensemble.v1",
        parameter_name="weight",
        data_type="float",
        default_value=0.5,
        lower_bound=0.0,
        upper_bound=1.0,
        discrete_candidates=None,
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=ENSEMBLE_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0, 1] inside fit_seasonal_ensemble; selected on validation only",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="seasonal_ensemble.v1",
        parameter_name="component_adapter",
        data_type="str",
        default_value=None,  # fit falls back to 'negbin_dglm.v1' when absent
        lower_bound=None,
        upper_bound=None,
        discrete_candidates=["negbin_dglm.v1", "seasonal_negbin_glm.v1", "fourier_negbin_glm.v1"],
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=ENSEMBLE_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="values outside discrete_candidates fall back to negbin_dglm.v1 inside fit_seasonal_ensemble",
        tunable=True,
    ),
)

_EXTENDED_CONTRACTS: dict[str, tuple[ParameterContract, ...]] = {
    "seasonal_negbin_glm.v1": SEASONAL_NEGBIN_GLM_CONTRACTS,
    "fourier_negbin_glm.v1": FOURIER_NEGBIN_GLM_CONTRACTS,
    "lagged_count_boosting.v1": LAGGED_COUNT_BOOSTING_CONTRACTS,
    "seasonal_ensemble.v1": SEASONAL_ENSEMBLE_CONTRACTS,
}


def all_contracts_for(adapter_id: str, frequency: str) -> list[ParameterContract]:
    """Contracts for any whitelisted adapter under a data-profile frequency.

    The four extended models serve their own contracts (frequency-independent:
    the seasonal period is resolved inside the fit). ``negbin_dglm.v1`` and
    ``seasonal_naive`` reuse ``kg_pt.contracts.contracts_for`` so provenance
    stays single-sourced. Unknown / parameterless adapters return [].
    """
    if adapter_id in _EXTENDED_CONTRACTS:
        return list(_EXTENDED_CONTRACTS[adapter_id])
    return contracts_for(adapter_id, frequency)


def registered_adapters() -> list[str]:
    """All whitelisted adapter ids: tunable candidates + baselines."""
    return list(WHITELIST_TUNABLE_ADAPTERS) + list(BASELINE_ADAPTER_IDS)


def adapter_registry_payload() -> dict[str, Any]:
    """Read-only registry summary for the Qwen planning context."""
    adapters: dict[str, Any] = {}
    for adapter_id in registered_adapters():
        adapters[adapter_id] = {
            "role": "tunable" if adapter_id in WHITELIST_TUNABLE_ADAPTERS else "baseline",
            "parameters": [asdict(c) for c in all_contracts_for(adapter_id, "month")],
        }
    return {
        "tunable_adapters": list(WHITELIST_TUNABLE_ADAPTERS),
        "baseline_adapters": list(BASELINE_ADAPTER_IDS),
        "adapters": adapters,
    }


def adapter_registry_slim(frequency: str = "month") -> dict[str, Any]:
    """Compact registry for the Qwen planning context (token-budget friendly).

    Per parameter only the fields Qwen must respect: name, type, default,
    bounds (or discrete candidates), transform and tunability. Frequency-aware
    so weekly profiles get the lag-52 seasonal contract.
    """
    adapters: dict[str, Any] = {}
    for adapter_id in registered_adapters():
        params: list[dict[str, Any]] = []
        for c in all_contracts_for(adapter_id, frequency):
            entry: dict[str, Any] = {
                "name": c.parameter_name,
                "type": c.data_type,
                "default": c.default_value,
                "transform": c.transform,
                "tunable": c.tunable,
            }
            if c.discrete_candidates is not None:
                entry["values"] = c.discrete_candidates
            else:
                entry["lower"] = c.lower_bound
                entry["upper"] = c.upper_bound
            params.append(entry)
        adapters[adapter_id] = {
            "role": "tunable" if adapter_id in WHITELIST_TUNABLE_ADAPTERS else "baseline",
            "parameters": params,
        }
    return {
        "tunable_adapters": list(WHITELIST_TUNABLE_ADAPTERS),
        "baseline_adapters": list(BASELINE_ADAPTER_IDS),
        "adapters": adapters,
    }

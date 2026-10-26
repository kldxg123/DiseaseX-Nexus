"""Parameter contracts: bounds, transforms and provenance for tunable knobs.

Every parameter carries an explicit ``parameter_source``. No contract in this
module may claim ``PAPER_EXTRACTED``: the knowledge graph currently holds no
paper evidence for these parameters, so implementation defaults are labelled
``IMPLEMENTATION_DEFAULT`` with ``missing_reason='NO_PAPER_EVIDENCE_IN_KG'``
and profile-derived fixed values are labelled ``DATA_PROFILE_RULE``.

Sampling is uniform over the (possibly log-transformed) range and uses only
the caller-provided ``random.Random`` instance, so a given seed reproduces the
exact same parameter sequence.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Any

from epidemic_core.kg_pt import NO_PAPER_EVIDENCE
from epidemic_core.kg_r3c.execution import sha256_json

SOURCE_PAPER_EXTRACTED = "PAPER_EXTRACTED"  # reserved by schema; never emitted here
SOURCE_IMPLEMENTATION_DEFAULT = "IMPLEMENTATION_DEFAULT"
SOURCE_DATA_PROFILE_RULE = "DATA_PROFILE_RULE"
SOURCE_OPTIMIZER_SELECTED = "OPTIMIZER_SELECTED"

NEGBIN_CODE_SOURCE = "src/epidemic_core/kg_r3c/execution.py:217-220"
SEASONAL_NAIVE_CODE_SOURCE = "src/epidemic_core/kg_r3c/execution.py:392"


@dataclass(frozen=True)
class ParameterContract:
    """One tunable (or explicitly fixed) model parameter, mirroring the
    ``epidemic_kg.kg_pt_parameter_contracts`` table."""

    adapter_id: str
    parameter_name: str
    data_type: str  # 'float' | 'int'
    default_value: float | None
    lower_bound: float | None
    upper_bound: float | None
    discrete_candidates: list[Any] | None
    transform: str  # 'none' | 'log'
    parameter_source: str
    evidence_locator: str | None
    code_source: str | None
    applicable_profile: dict[str, Any]
    missing_reason: str | None
    safety_constraint: str | None
    tunable: bool


NEGBIN_CONTRACTS: tuple[ParameterContract, ...] = (
    ParameterContract(
        adapter_id="negbin_dglm.v1",
        parameter_name="discount",
        data_type="float",
        default_value=0.95,
        lower_bound=0.8,
        upper_bound=0.999,
        discrete_candidates=None,
        transform="none",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=NEGBIN_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0.8, 0.999] inside fit_negbin_dglm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="negbin_dglm.v1",
        parameter_name="shape",
        data_type="float",
        default_value=10.0,
        lower_bound=0.5,
        upper_bound=500.0,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=NEGBIN_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0.5, 500] inside fit_negbin_dglm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="negbin_dglm.v1",
        parameter_name="prior_shape",
        data_type="float",
        default_value=1.0,
        lower_bound=0.01,
        upper_bound=100.0,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=NEGBIN_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0.01, 100] inside fit_negbin_dglm",
        tunable=True,
    ),
    ParameterContract(
        adapter_id="negbin_dglm.v1",
        parameter_name="prior_rate",
        data_type="float",
        default_value=1.0,
        lower_bound=0.01,
        upper_bound=100.0,
        discrete_candidates=None,
        transform="log",
        parameter_source=SOURCE_IMPLEMENTATION_DEFAULT,
        evidence_locator=None,
        code_source=NEGBIN_CODE_SOURCE,
        applicable_profile={},
        missing_reason=NO_PAPER_EVIDENCE,
        safety_constraint="clipped to [0.01, 100] inside fit_negbin_dglm",
        tunable=True,
    ),
)

SEASONAL_NAIVE_MONTH_CONTRACTS: tuple[ParameterContract, ...] = (
    ParameterContract(
        adapter_id="seasonal_naive",
        parameter_name="season_lag",
        data_type="int",
        default_value=12,
        lower_bound=12,
        upper_bound=12,
        discrete_candidates=[12],
        transform="none",
        parameter_source=SOURCE_DATA_PROFILE_RULE,
        evidence_locator=None,
        code_source=SEASONAL_NAIVE_CODE_SOURCE,
        applicable_profile={"frequency": "month"},
        missing_reason=None,
        safety_constraint="fixed by data profile rule: monthly seasonality implies lag 12",
        tunable=False,
    ),
)

_CONTRACTS_BY_ADAPTER: dict[str, tuple[ParameterContract, ...]] = {
    "negbin_dglm.v1": NEGBIN_CONTRACTS,
    "seasonal_naive": SEASONAL_NAIVE_MONTH_CONTRACTS,
}


def contracts_for(adapter_id: str, frequency: str) -> list[ParameterContract]:
    """Contracts applicable to an adapter under a data profile frequency.

    The seasonal-naive lag rule only applies to monthly profiles; adapters
    without registered tunable parameters (e.g. last_value/drift) return an
    empty list, which the tuner treats as "defaults only".
    """
    contracts = list(_CONTRACTS_BY_ADAPTER.get(adapter_id, ()))
    return [c for c in contracts if not c.applicable_profile or c.applicable_profile.get("frequency") == frequency]


def _contract_payload(contract: ParameterContract) -> dict[str, Any]:
    return asdict(contract)


def contract_sha256(contracts: list[ParameterContract]) -> str:
    """Content hash of an ordered contract list (sorted for stability)."""
    payload = sorted((_contract_payload(c) for c in contracts), key=lambda p: (p["adapter_id"], p["parameter_name"]))
    return sha256_json(payload)


def default_params(contracts: list[ParameterContract]) -> dict[str, float]:
    """Default parameter bundle: exactly the contract defaults."""
    return {c.parameter_name: c.default_value for c in contracts if c.default_value is not None}


def sample_params(contracts: list[ParameterContract], rng: random.Random) -> dict[str, float]:
    """Sample one parameter set inside contract bounds.

    ``transform='log'`` samples uniformly in log space (log-uniform over the
    range); every other parameter samples uniformly. Non-tunable contracts
    keep their default value. All draws come from ``rng`` only.
    """
    params: dict[str, float] = {}
    for c in contracts:
        if not c.tunable or c.lower_bound is None or c.upper_bound is None:
            if c.default_value is not None:
                params[c.parameter_name] = c.default_value
            continue
        if c.transform == "log":
            value = math.exp(rng.uniform(math.log(c.lower_bound), math.log(c.upper_bound)))
        else:
            value = rng.uniform(c.lower_bound, c.upper_bound)
        if c.data_type == "int":
            value = float(round(value))
        params[c.parameter_name] = value
    return params


def clip_params(contracts: list[ParameterContract], params: dict[str, Any]) -> dict[str, float]:
    """Clip a parameter set to contract bounds; unknown keys pass through."""
    by_name = {c.parameter_name: c for c in contracts}
    out: dict[str, float] = {}
    for name, value in params.items():
        c = by_name.get(name)
        if c is None or value is None:
            out[name] = value
            continue
        v = float(value)
        if c.lower_bound is not None:
            v = max(v, float(c.lower_bound))
        if c.upper_bound is not None:
            v = min(v, float(c.upper_bound))
        if c.data_type == "int":
            v = float(round(v))
        out[name] = v
    return out


def parameter_source_map(contracts: list[ParameterContract]) -> dict[str, str]:
    """parameter name -> provenance source, for trial persistence."""
    return {c.parameter_name: c.parameter_source for c in contracts}

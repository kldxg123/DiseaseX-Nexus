"""Qwen tuning-plan output contract and deterministic validator (SPEC section 3.4).

Safety invariants enforced here:

  * a plan is accepted only after passing ``validate_plan`` with zero errors;
    budget fields above the SPEC caps are rejected, never silently clamped
    (the execution layer clamps defensively on top);
  * candidate models and parameters must resolve against the registered
    parameter contracts (``contracts_ext``); nothing unregistered is tunable;
  * structured fields (adapter/parameter/transform/source/evidence_ref) are
    full-match whitelist validated; free-text fields are regex scanned for
    SQL, shell, paths, URLs, promotion requests, test-label references and
    prompt-injection phrases;
  * ``PAPER_EXTRACTED`` is never an acceptable parameter source: the KG holds
    no paper evidence for these parameters;
  * ``deterministic_fallback_plan`` always yields a plan that passes this
    validator, so the loop has a safe default when Qwen output is unusable.

This module is pure stdlib and imports cleanly without a database.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from typing import Any, TypeGuard

from epidemic_core.kg_pt.contracts import ParameterContract
from epidemic_core.kg_qwen import (
    BASELINE_IMPROVEMENT_MIN,
    COVERAGE_MIN,
    MAX_ROUNDS,
    MAX_TOTAL_MINUTES,
    MAX_TRIALS_PER_ROUND,
    TARGET_WAPE,
)
from epidemic_core.kg_qwen.contracts_ext import WHITELIST_TUNABLE_ADAPTERS, all_contracts_for

PLAN_VALIDATOR_VERSION = "kq_plan_validator.v1"

PLAN_VERSION = "v1"
PRIMARY_METRIC = "WAPE"

VALID_TRANSFORMS = ("none", "log")
VALID_PARAMETER_SOURCES = ("IMPLEMENTATION_DEFAULT", "DATA_PROFILE_RULE", "OPTIMIZER_SELECTED")
FORBIDDEN_PARAMETER_SOURCE = "PAPER_EXTRACTED"

# Structured identifier fields (adapter_id, parameter_name, evidence_ref,
# feature-recipe names) must full-match this charset: letters, digits and
# ``_:.-`` only — crucially no ``/``, ``\\``, whitespace or shell metachars.
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_:.\-]+$")

# Free-text forbidden patterns. Chinese prose does not normally contain any of
# these; every hit rejects the plan.
_SQL_RE = re.compile(
    r"\b(select|insert|update|delete|drop|alter|grant|truncate)\b.*\b(from|into|table|where)\b",
    re.IGNORECASE | re.DOTALL,
)
_SHELL_PATTERNS: tuple[str, ...] = (
    r"`",
    r"\$\(",
    r"&&",
    r"\|\|",
    r";",
    r"\|",
    r">",
    r"<",
    r"\bsudo\b",
    r"\brm\s+-",
    r"\bchmod\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bexec\b",
)
_SHELL_RE = re.compile("|".join(f"(?:{p})" for p in _SHELL_PATTERNS), re.IGNORECASE)
_PATH_RE = re.compile(r"(?:/|\\|~|\.py\b|\.sh\b)")
_URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_PROMOTION_RE = re.compile(r"(?:promotion|promote|active.?pointer|approval)", re.IGNORECASE)
_TEST_LABEL_RE = re.compile(r"test.?label", re.IGNORECASE)
_INJECTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous\s+instructions|忽略(?:之前|以上|上述)(?:的)?指令|disregard)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlanError:
    """One validation failure: stable machine code, human message, JSON path."""

    code: str
    message: str
    path: str


def _err(code: str, message: str, path: str) -> PlanError:
    return PlanError(code=code, message=message, path=path)


def parse_plan_json(text: str) -> dict[str, Any]:
    """Extract the first balanced ``{...}`` JSON object from model output.

    The scan counts braces while skipping string literals (with backslash
    escapes), so braces inside quoted text do not affect balancing. Raises
    ``ValueError("PLAN_JSON_PARSE:...")`` when no balanced object exists or
    ``json.loads`` fails.
    """
    start = -1
    depth = 0
    in_string = False
    escaped = False
    end = -1
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                end = i
                break
    if start < 0 or end < 0:
        raise ValueError("PLAN_JSON_PARSE: no balanced JSON object found in model output")
    fragment = text[start : end + 1]
    try:
        parsed = json.loads(fragment)
    except json.JSONDecodeError as exc:
        raise ValueError(f"PLAN_JSON_PARSE: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("PLAN_JSON_PARSE: top-level JSON value is not an object")
    return parsed


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> TypeGuard[float | int]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _scan_free_text(value: Any, path: str) -> list[PlanError]:
    """Regex scan of one free-text field; every pattern hit rejects the plan."""
    if not isinstance(value, str):
        return [_err("PLAN_SCHEMA_INVALID", f"expected string free text, got {type(value).__name__}", path)]
    errors: list[PlanError] = []
    checks = (
        (_SQL_RE, "SQL-like statement"),
        (_SHELL_RE, "shell metacharacter or command"),
        (_PATH_RE, "filesystem path"),
        (_URL_RE, "URL"),
        (_PROMOTION_RE, "promotion/approval request"),
        (_INJECTION_RE, "prompt-injection phrase"),
    )
    for pattern, label in checks:
        m = pattern.search(value)
        if m:
            errors.append(
                _err("PLAN_FORBIDDEN_CONTENT", f"forbidden content ({label}): {m.group(0)!r}", path)
            )
    if _TEST_LABEL_RE.search(value):
        errors.append(
            _err("PLAN_TEST_LABEL_REFERENCE", "reference to test labels is forbidden", path)
        )
    return errors


def _scan_identifier(value: Any, path: str, what: str) -> list[PlanError]:
    """Full-match whitelist scan of a structured identifier field."""
    if not isinstance(value, str):
        return [_err("PLAN_SCHEMA_INVALID", f"{what} must be a string", path)]
    if not _SAFE_IDENTIFIER_RE.match(value):
        return [
            _err(
                "PLAN_FORBIDDEN_CONTENT",
                f"{what} contains characters outside [A-Za-z0-9_:.-]",
                path,
            )
        ]
    return []


def _validate_objective(plan: dict[str, Any]) -> list[PlanError]:
    path = "objective"
    obj = plan.get("objective")
    if not isinstance(obj, dict):
        return [_err("PLAN_SCHEMA_INVALID", "objective must be an object", path)]
    errors: list[PlanError] = []
    if obj.get("primary_metric") != PRIMARY_METRIC:
        errors.append(_err("PLAN_SCHEMA_INVALID", "objective.primary_metric must be 'WAPE'", f"{path}.primary_metric"))
    target_max = obj.get("target_max")
    if not _is_number(target_max):
        errors.append(_err("PLAN_SCHEMA_INVALID", "objective.target_max must be a finite number", f"{path}.target_max"))
    elif float(target_max) > TARGET_WAPE:
        errors.append(
            _err(
                "PLAN_SCHEMA_INVALID",
                f"objective.target_max {target_max} is looser than the required {TARGET_WAPE}",
                f"{path}.target_max",
            )
        )
    coverage_min = obj.get("interval_coverage_min")
    if not _is_number(coverage_min):
        errors.append(
            _err(
                "PLAN_SCHEMA_INVALID",
                "objective.interval_coverage_min must be a finite number",
                f"{path}.interval_coverage_min",
            )
        )
    elif float(coverage_min) < COVERAGE_MIN:
        errors.append(
            _err(
                "PLAN_SCHEMA_INVALID",
                f"objective.interval_coverage_min {coverage_min} below required {COVERAGE_MIN}",
                f"{path}.interval_coverage_min",
            )
        )
    improvement_min = obj.get("baseline_improvement_min")
    if not _is_number(improvement_min):
        errors.append(
            _err(
                "PLAN_SCHEMA_INVALID",
                "objective.baseline_improvement_min must be a finite number",
                f"{path}.baseline_improvement_min",
            )
        )
    elif float(improvement_min) < BASELINE_IMPROVEMENT_MIN:
        errors.append(
            _err(
                "PLAN_SCHEMA_INVALID",
                f"objective.baseline_improvement_min {improvement_min} below required {BASELINE_IMPROVEMENT_MIN}",
                f"{path}.baseline_improvement_min",
            )
        )
    return errors


def _validate_parameter_space(
    entry: Any,
    index: int,
    frequency: str,
) -> list[PlanError]:
    path = f"parameter_spaces[{index}]"
    if not isinstance(entry, dict):
        return [_err("PLAN_SCHEMA_INVALID", "parameter space entry must be an object", path)]
    errors: list[PlanError] = []

    adapter_id = entry.get("adapter_id")
    parameter_name = entry.get("parameter_name")
    errors.extend(_scan_identifier(adapter_id, f"{path}.adapter_id", "adapter_id"))
    errors.extend(_scan_identifier(parameter_name, f"{path}.parameter_name", "parameter_name"))

    transform = entry.get("transform")
    if transform not in VALID_TRANSFORMS:
        errors.append(
            _err("PLAN_SCHEMA_INVALID", f"transform must be one of {VALID_TRANSFORMS}", f"{path}.transform")
        )
    source = entry.get("parameter_source")
    if source == FORBIDDEN_PARAMETER_SOURCE:
        errors.append(
            _err(
                "PLAN_FORBIDDEN_CONTENT",
                "PAPER_EXTRACTED may never be claimed: the KG holds no paper evidence",
                f"{path}.parameter_source",
            )
        )
    elif source not in VALID_PARAMETER_SOURCES:
        errors.append(
            _err(
                "PLAN_SCHEMA_INVALID",
                f"parameter_source must be one of {VALID_PARAMETER_SOURCES}",
                f"{path}.parameter_source",
            )
        )

    evidence_ref = entry.get("evidence_ref")
    errors.extend(_scan_identifier(evidence_ref, f"{path}.evidence_ref", "evidence_ref"))

    rationale = entry.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append(_err("PLAN_SCHEMA_INVALID", "rationale must be a non-empty string", f"{path}.rationale"))
    else:
        errors.extend(_scan_free_text(rationale, f"{path}.rationale"))

    has_numeric = "lower" in entry or "upper" in entry
    has_discrete = "values" in entry
    if not has_numeric and not has_discrete:
        errors.append(
            _err("PLAN_SCHEMA_INVALID", "entry needs numeric lower/upper or discrete values", path)
        )
    lower = entry.get("lower")
    upper = entry.get("upper")
    if has_numeric:
        if not _is_number(lower) or not _is_number(upper):
            errors.append(
                _err("PLAN_SCHEMA_INVALID", "lower and upper must both be finite numbers", path)
            )
        elif float(lower) > float(upper):
            errors.append(
                _err("PLAN_SCHEMA_INVALID", f"lower {lower} greater than upper {upper}", path)
            )
    values = entry.get("values")
    if has_discrete:
        if not isinstance(values, list) or not values:
            errors.append(_err("PLAN_SCHEMA_INVALID", "values must be a non-empty list", f"{path}.values"))
        elif not all(_is_number(v) or isinstance(v, str) for v in values):
            errors.append(
                _err("PLAN_SCHEMA_INVALID", "values entries must be numbers or strings", f"{path}.values")
            )

    # Registry checks need clean identifiers first.
    if errors:
        return errors
    assert isinstance(adapter_id, str) and isinstance(parameter_name, str)

    if adapter_id not in WHITELIST_TUNABLE_ADAPTERS:
        errors.append(
            _err(
                "PLAN_UNREGISTERED_MODEL",
                f"adapter {adapter_id!r} is not in WHITELIST_TUNABLE_ADAPTERS",
                f"{path}.adapter_id",
            )
        )
        return errors

    contracts = all_contracts_for(adapter_id, frequency)
    contract = next((c for c in contracts if c.parameter_name == parameter_name), None)
    if contract is None:
        errors.append(
            _err(
                "PLAN_UNREGISTERED_PARAMETER",
                f"parameter {parameter_name!r} is not registered for adapter {adapter_id!r}",
                f"{path}.parameter_name",
            )
        )
        return errors

    if isinstance(transform, str) and transform != contract.transform:
        errors.append(
            _err(
                "PLAN_SCHEMA_INVALID",
                f"transform {transform!r} does not match contract transform {contract.transform!r}",
                f"{path}.transform",
            )
        )

    if contract.discrete_candidates is not None:
        # Discrete contract: the plan must narrow via values drawn from the
        # contract candidates.
        if has_numeric:
            errors.append(
                _err(
                    "PLAN_DISCRETE_VIOLATION",
                    f"contract for {parameter_name!r} is discrete; numeric lower/upper not allowed",
                    path,
                )
            )
        if isinstance(values, list):
            for v in values:
                if v not in contract.discrete_candidates:
                    errors.append(
                        _err(
                            "PLAN_DISCRETE_VIOLATION",
                            f"value {v!r} not in contract candidates {contract.discrete_candidates!r}",
                            f"{path}.values",
                        )
                    )
    else:
        # Numeric contract: bounds must stay inside the contract bounds.
        if has_numeric and _is_number(lower) and _is_number(upper):
            if contract.lower_bound is not None and float(lower) < float(contract.lower_bound):
                errors.append(
                    _err(
                        "PLAN_BOUND_VIOLATION",
                        f"lower {lower} below contract lower bound {contract.lower_bound}",
                        f"{path}.lower",
                    )
                )
            if contract.upper_bound is not None and float(upper) > float(contract.upper_bound):
                errors.append(
                    _err(
                        "PLAN_BOUND_VIOLATION",
                        f"upper {upper} above contract upper bound {contract.upper_bound}",
                        f"{path}.upper",
                    )
                )
        if isinstance(values, list):
            for v in values:
                if not _is_number(v):
                    errors.append(
                        _err(
                            "PLAN_DISCRETE_VIOLATION",
                            f"non-numeric value {v!r} for numeric contract {parameter_name!r}",
                            f"{path}.values",
                        )
                    )
                    continue
                if contract.lower_bound is not None and float(v) < float(contract.lower_bound):
                    errors.append(
                        _err(
                            "PLAN_DISCRETE_VIOLATION",
                            f"value {v} below contract lower bound {contract.lower_bound}",
                            f"{path}.values",
                        )
                    )
                if contract.upper_bound is not None and float(v) > float(contract.upper_bound):
                    errors.append(
                        _err(
                            "PLAN_DISCRETE_VIOLATION",
                            f"value {v} above contract upper bound {contract.upper_bound}",
                            f"{path}.values",
                        )
                    )
    return errors


def _validate_feature_recipes(plan: dict[str, Any]) -> list[PlanError]:
    recipes = plan.get("feature_recipes")
    if not isinstance(recipes, list):
        return [_err("PLAN_SCHEMA_INVALID", "feature_recipes must be a list", "feature_recipes")]
    errors: list[PlanError] = []
    for i, recipe in enumerate(recipes):
        path = f"feature_recipes[{i}]"
        if not isinstance(recipe, dict):
            errors.append(_err("PLAN_SCHEMA_INVALID", "feature recipe must be an object", path))
            continue
        errors.extend(_scan_identifier(recipe.get("name"), f"{path}.name", "feature recipe name"))
    return errors


def validate_plan(
    plan: dict[str, Any],
    *,
    expected_snapshot_sha256: str,
    frequency: str,
) -> list[PlanError]:
    """Hand-written schema + safety validation of one tuning plan.

    Returns an empty list when the plan is acceptable. All violations are
    collected (no fail-fast) so the caller can feed the complete error list
    back to the planner for correction.
    """
    if not isinstance(plan, dict):
        return [_err("PLAN_SCHEMA_INVALID", "plan must be a JSON object", "$")]

    errors: list[PlanError] = []

    if plan.get("plan_version") != PLAN_VERSION:
        errors.append(_err("PLAN_SCHEMA_INVALID", "plan_version must be 'v1'", "plan_version"))

    snapshot = plan.get("dataset_snapshot_sha256")
    if not isinstance(snapshot, str) or not snapshot:
        errors.append(
            _err("PLAN_SCHEMA_INVALID", "dataset_snapshot_sha256 must be a non-empty string", "dataset_snapshot_sha256")
        )
    elif snapshot != expected_snapshot_sha256:
        errors.append(
            _err(
                "PLAN_SNAPSHOT_MISMATCH",
                "dataset_snapshot_sha256 does not equal the frozen snapshot sha256",
                "dataset_snapshot_sha256",
            )
        )

    errors.extend(_validate_objective(plan))

    candidates = plan.get("candidate_models")
    if not isinstance(candidates, list) or not candidates or not all(isinstance(c, str) for c in candidates):
        errors.append(
            _err("PLAN_SCHEMA_INVALID", "candidate_models must be a non-empty list of strings", "candidate_models")
        )
    else:
        for i, candidate in enumerate(candidates):
            if candidate not in WHITELIST_TUNABLE_ADAPTERS:
                errors.append(
                    _err(
                        "PLAN_UNREGISTERED_MODEL",
                        f"candidate model {candidate!r} is not in WHITELIST_TUNABLE_ADAPTERS",
                        f"candidate_models[{i}]",
                    )
                )

    spaces = plan.get("parameter_spaces")
    if not isinstance(spaces, list):
        errors.append(_err("PLAN_SCHEMA_INVALID", "parameter_spaces must be a list", "parameter_spaces"))
    else:
        for i, entry in enumerate(spaces):
            errors.extend(_validate_parameter_space(entry, i, frequency))

    errors.extend(_validate_feature_recipes(plan))

    # Budgets: rejected (not clamped) when above the SPEC caps.
    budgets = (
        ("max_rounds", MAX_ROUNDS, True),
        ("max_trials_per_round", MAX_TRIALS_PER_ROUND, True),
        ("max_total_minutes", MAX_TOTAL_MINUTES, False),
    )
    for field, cap, integer in budgets:
        value = plan.get(field)
        if integer and not _is_int(value):
            errors.append(_err("PLAN_SCHEMA_INVALID", f"{field} must be an integer", field))
        elif not integer and not _is_number(value):
            errors.append(_err("PLAN_SCHEMA_INVALID", f"{field} must be a finite number", field))
        elif _is_number(value) or _is_int(value):
            if float(value) < 1:
                errors.append(_err("PLAN_SCHEMA_INVALID", f"{field} must be >= 1", field))
            elif float(value) > cap:
                errors.append(
                    _err("PLAN_BUDGET_VIOLATION", f"{field} {value} exceeds budget cap {cap}", field)
                )

    for field in ("rationale_zh", "kg_evidence_ids", "known_risks", "stop_conditions"):
        items = plan.get(field)
        if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
            errors.append(_err("PLAN_SCHEMA_INVALID", f"{field} must be a list of strings", field))
            continue
        for i, item in enumerate(items):
            errors.extend(_scan_free_text(item, f"{field}[{i}]"))

    return errors


def narrowed_contracts(plan: dict[str, Any], frequency: str) -> dict[str, list[ParameterContract]]:
    """Per-candidate contracts with plan-narrowed search spaces.

    For every candidate model the base is ``contracts_ext.all_contracts_for``.
    Parameters mentioned in ``parameter_spaces`` get narrowed bounds (or a
    narrowed discrete candidate list); tunable parameters not mentioned are
    frozen at their default (``tunable=False``, ``lower == upper == default``).
    Unregistered candidates/parameters are ignored here — ``validate_plan``
    is the gate that rejects them.
    """
    spaces_by_adapter: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in plan.get("parameter_spaces") or []:
        if not isinstance(entry, dict):
            continue
        adapter_id = entry.get("adapter_id")
        parameter_name = entry.get("parameter_name")
        if isinstance(adapter_id, str) and isinstance(parameter_name, str):
            spaces_by_adapter.setdefault(adapter_id, {})[parameter_name] = entry

    narrowed: dict[str, list[ParameterContract]] = {}
    candidates = plan.get("candidate_models") or []
    for adapter_id in candidates:
        if not isinstance(adapter_id, str) or adapter_id not in WHITELIST_TUNABLE_ADAPTERS:
            continue
        entries = spaces_by_adapter.get(adapter_id, {})
        result: list[ParameterContract] = []
        for contract in all_contracts_for(adapter_id, frequency):
            entry = entries.get(contract.parameter_name)
            if entry is None:
                if contract.tunable:
                    default = contract.default_value
                    result.append(
                        replace(
                            contract,
                            tunable=False,
                            lower_bound=default,
                            upper_bound=default,
                            discrete_candidates=[default] if default is not None else contract.discrete_candidates,
                        )
                    )
                else:
                    result.append(contract)
                continue
            source = entry.get("parameter_source")
            evidence_ref = entry.get("evidence_ref")
            common: dict[str, Any] = {"tunable": True}
            if isinstance(source, str) and source in VALID_PARAMETER_SOURCES:
                common["parameter_source"] = source
            if isinstance(evidence_ref, str) and _SAFE_IDENTIFIER_RE.match(evidence_ref):
                common["evidence_locator"] = evidence_ref
            values = entry.get("values")
            if contract.discrete_candidates is not None:
                if isinstance(values, list):
                    candidates_list = [v for v in values if v in contract.discrete_candidates]
                else:
                    candidates_list = []
                result.append(replace(contract, discrete_candidates=candidates_list, **common))
            elif isinstance(values, list) and values:
                numeric_values = [float(v) for v in values if _is_number(v)]
                result.append(replace(contract, discrete_candidates=numeric_values, **common))
            else:
                lower = entry.get("lower")
                upper = entry.get("upper")
                if _is_number(lower) and _is_number(upper):
                    result.append(
                        replace(contract, lower_bound=float(lower), upper_bound=float(upper), **common)
                    )
                else:
                    result.append(contract)
        narrowed[adapter_id] = result
    return narrowed


_REASON_UNSAFE_RE = re.compile(r"[^0-9A-Za-z_一-鿿.,:?()=%\s]+")


def _sanitize_reason(reason: str) -> str:
    """Strip anything that could trip the free-text scanner from a reason string."""
    cleaned = _REASON_UNSAFE_RE.sub(" ", str(reason))
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_compact_plan(raw: dict[str, Any], *, snapshot_sha256: str, frequency: str) -> dict[str, Any]:
    """Expand Qwen's compact plan output into the full plan contract.

    The platform owns the fixed fields (objective, budgets, feature_recipes,
    kg_evidence_ids) so Qwen's token budget only carries the creative content:
    candidate models, parameter spaces and rationales. The snapshot sha is
    overwritten with the expected frozen snapshot — never trusted from output.

    Parameter-space entries that name a REGISTERED adapter+parameter but omit
    bounds are completed with the full contract range (or discrete candidates);
    every completion is recorded in ``normalization_notes`` for audit. Entries
    with unregistered names are left untouched so the validator rejects them.
    """
    notes: list[str] = []
    spaces: list[Any] = []
    for entry in raw.get("parameter_spaces", []) or []:
        if not isinstance(entry, dict):
            spaces.append(entry)
            continue
        space = dict(entry)
        adapter_id = str(space.get("adapter_id") or "")
        parameter_name = str(space.get("parameter_name") or "")
        contract = next(
            (
                c
                for c in all_contracts_for(adapter_id, frequency)
                if c.parameter_name == parameter_name
            ),
            None,
        )
        has_bounds = space.get("lower") is not None and space.get("upper") is not None
        has_values = isinstance(space.get("values"), list) and bool(space.get("values"))
        if contract is not None and not has_bounds and not has_values:
            if contract.discrete_candidates is not None:
                space["values"] = list(contract.discrete_candidates)
            else:
                space["lower"] = contract.lower_bound
                space["upper"] = contract.upper_bound
            notes.append(f"NORMALIZED_DEFAULT_BOUNDS:{adapter_id}:{parameter_name}")
        spaces.append(space)
    plan: dict[str, Any] = {
        "plan_version": raw.get("plan_version", PLAN_VERSION),
        "dataset_snapshot_sha256": snapshot_sha256,
        "objective": {
            "primary_metric": PRIMARY_METRIC,
            "target_max": TARGET_WAPE,
            "interval_coverage_min": COVERAGE_MIN,
            "baseline_improvement_min": BASELINE_IMPROVEMENT_MIN,
        },
        "candidate_models": raw.get("candidate_models", []),
        "parameter_spaces": spaces,
        "normalization_notes": notes,
        "feature_recipes": raw.get("feature_recipes", []),
        "max_rounds": MAX_ROUNDS,
        "max_trials_per_round": 24,
        "max_total_minutes": MAX_TOTAL_MINUTES,
        "rationale_zh": raw.get("rationale_zh", []),
        "kg_evidence_ids": raw.get("kg_evidence_ids", []),
        "known_risks": raw.get("known_risks", []),
        "stop_conditions": raw.get("stop_conditions", []),
    }
    return plan


def deterministic_fallback_plan(snapshot_sha256: str, frequency: str, reason: str) -> dict[str, Any]:
    """A valid default plan used when Qwen output is unusable.

    Candidates are the three GLM-family adapters; every tunable parameter is
    searched over its full contract width with ``IMPLEMENTATION_DEFAULT``
    provenance (PAPER_EXTRACTED is never claimed). The plan is constructed to
    pass ``validate_plan`` for the given snapshot/frequency.
    """
    safe_reason = _sanitize_reason(reason) or "UNKNOWN"
    candidates = ["negbin_dglm.v1", "seasonal_negbin_glm.v1", "fourier_negbin_glm.v1"]
    parameter_spaces: list[dict[str, Any]] = []
    for adapter_id in candidates:
        for contract in all_contracts_for(adapter_id, frequency):
            if not contract.tunable:
                continue
            entry: dict[str, Any] = {
                "adapter_id": adapter_id,
                "parameter_name": contract.parameter_name,
                "transform": contract.transform,
                "parameter_source": "IMPLEMENTATION_DEFAULT",
                "rationale": "确定性回退：采用合同默认全宽搜索空间，来源为实现默认。",
                "evidence_ref": f"kg_qwen.contracts_ext:{adapter_id}.{contract.parameter_name}",
            }
            if contract.discrete_candidates is not None:
                entry["values"] = list(contract.discrete_candidates)
            else:
                entry["lower"] = contract.lower_bound
                entry["upper"] = contract.upper_bound
            parameter_spaces.append(entry)
    return {
        "plan_version": PLAN_VERSION,
        "dataset_snapshot_sha256": snapshot_sha256,
        "objective": {
            "primary_metric": PRIMARY_METRIC,
            "target_max": TARGET_WAPE,
            "interval_coverage_min": COVERAGE_MIN,
            "baseline_improvement_min": BASELINE_IMPROVEMENT_MIN,
        },
        "candidate_models": candidates,
        "parameter_spaces": parameter_spaces,
        "feature_recipes": [],
        "max_rounds": MAX_ROUNDS,
        "max_trials_per_round": 24,
        "max_total_minutes": MAX_TOTAL_MINUTES,
        "rationale_zh": [
            "本计划为确定性回退方案：Qwen 输出无效或未通过校验，"
            f"回退原因：{safe_reason}。"
            "候选模型与参数空间均取自已注册合同的默认全宽范围，不声明任何论文提取来源。"
        ],
        "kg_evidence_ids": [],
        "known_risks": ["回退方案未利用模型智能推理，参数组合可能不是最优。"],
        "stop_conditions": ["达到最大轮次或总时长预算即停止调优。"],
    }

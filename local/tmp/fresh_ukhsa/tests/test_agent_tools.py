"""Tests for the forecast-tuning-agent tool layer (SPEC section 3.6)."""

from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any

import pytest

from epidemic_core.kg_pt.split import TemporalSplit
from epidemic_core.kg_qwen import agent_tools as at
from epidemic_core.kg_qwen.plan_contract import deterministic_fallback_plan
from epidemic_core.kg_r3c.execution import TrainingRow

SNAPSHOT = "c" * 64


def make_rows(values: list[float | None], start: date = date(2020, 1, 1)) -> list[TrainingRow]:
    return [
        TrainingRow(period_start=start + timedelta(days=7 * i), period_end=None, value=v)
        for i, v in enumerate(values)
    ]


def make_split() -> TemporalSplit:
    return TemporalSplit(
        n_rows=60,
        train_n=40,
        val_n=12,
        test_n=8,
        train_start=date(2020, 1, 1),
        train_end=date(2020, 10, 1),
        validation_start=date(2020, 11, 1),
        validation_end=date(2021, 1, 1),
        test_start=date(2021, 2, 1),
        test_end=date(2021, 3, 1),
        train_rows=40,
        validation_rows=12,
        test_rows=8,
        split_sha256="d" * 64,
    )


def make_ctx(**overrides: Any) -> at.ToolContext:
    kwargs: dict[str, Any] = {
        "track_key": "FIN:hantavirus:puumala_virus_reports:week",
        "frequency": "week",
        "trainval_rows": make_rows([float(i % 7 + 1) for i in range(60)]),
        "snapshot_sha256": SNAPSHOT,
        "split": make_split(),
        "validation_summaries": [
            {"round_index": 1, "best_val_wape": 0.42, "gate_pass": False},
            {"round_index": 2, "best_val_wape": 0.31, "gate_pass": True},
        ],
    }
    kwargs.update(overrides)
    return at.ToolContext(**kwargs)


class TestDatasetProfile:
    def test_hand_computed_statistics(self) -> None:
        rows = make_rows([1.0, None, 3.0, 0.0])
        profile = at.dataset_profile(rows, "week")
        assert profile["n_obs"] == 4
        assert profile["missing_count"] == 1
        assert profile["missing_ratio"] == pytest.approx(0.25)
        assert profile["zero_ratio"] == pytest.approx(1 / 3)
        assert profile["mean"] == pytest.approx(4 / 3)
        # population variance of [1, 3, 0]: mean 4/3
        expected_var = ((1 - 4 / 3) ** 2 + (3 - 4 / 3) ** 2 + (0 - 4 / 3) ** 2) / 3
        assert profile["variance"] == pytest.approx(expected_var)
        assert profile["overdispersion_ratio"] == pytest.approx(expected_var / (4 / 3))
        assert profile["seasonal_lag"] == 52
        assert profile["first_period"] == "2020-01-01"
        assert profile["last_period"] == "2020-01-22"

    def test_month_frequency_seasonal_lag(self) -> None:
        profile = at.dataset_profile(make_rows([1.0] * 30), "month")
        assert profile["seasonal_lag"] == 12

    def test_perfect_linear_trend_autocorrelation(self) -> None:
        rows = make_rows([float(i) for i in range(1, 61)])
        profile = at.dataset_profile(rows, "month")
        assert profile["seasonal_autocorrelation"] == pytest.approx(1.0)
        # trend_ratio: mean(37..60) / mean(13..36) = 48.5 / 24.5
        assert profile["trend_ratio"] == pytest.approx(48.5 / 24.5)

    def test_trend_ratio_none_with_insufficient_history(self) -> None:
        profile = at.dataset_profile(make_rows([1.0] * 30), "week")
        assert profile["trend_ratio"] is None

    def test_autocorrelation_skips_missing_pairs(self) -> None:
        values = [float(i) for i in range(60)]
        values[12] = None  # kills pairs (0,12) and (12,24) at lag 12
        profile = at.dataset_profile(make_rows(values), "month")
        assert profile["seasonal_autocorrelation"] == pytest.approx(1.0)

    def test_pure_function_no_mutation(self) -> None:
        rows = make_rows([2.0, None, 4.0])
        before = copy.deepcopy(rows)
        first = at.dataset_profile(rows, "week")
        second = at.dataset_profile(rows, "week")
        assert first == second
        assert rows == before

    def test_empty_rows(self) -> None:
        profile = at.dataset_profile([], "month")
        assert profile["n_obs"] == 0
        assert profile["mean"] is None
        assert profile["missing_ratio"] is None


class TestAllowedTools:
    def test_get_dataset_profile(self) -> None:
        ctx = make_ctx()
        result = at.dispatch_tool("get_dataset_profile", {"track_key": ctx.track_key}, ctx)
        assert result["n_obs"] == 60
        assert "error" not in result

    def test_get_kg_model_candidates(self) -> None:
        result = at.dispatch_tool("get_kg_model_candidates", {}, make_ctx())
        assert result["tunable_candidates"] == [
            "negbin_dglm.v1",
            "seasonal_negbin_glm.v1",
            "fourier_negbin_glm.v1",
            "lagged_count_boosting.v1",
            "seasonal_ensemble.v1",
        ]
        assert "registry" in result

    def test_get_parameter_contracts(self) -> None:
        result = at.dispatch_tool("get_parameter_contracts", {"adapter_id": "negbin_dglm.v1"}, make_ctx())
        names = {c["parameter_name"] for c in result["contracts"]}
        assert {"discount", "shape", "prior_shape", "prior_rate"} <= names

    def test_get_parameter_contracts_unknown_adapter(self) -> None:
        result = at.dispatch_tool("get_parameter_contracts", {"adapter_id": "evil"}, make_ctx())
        assert result["error"] == "ADAPTER_NOT_REGISTERED"

    def test_get_experiment_history(self) -> None:
        ctx = make_ctx()
        result = at.dispatch_tool("get_experiment_history", {}, ctx)
        assert result["rounds"] == ctx.validation_summaries

    def test_get_validation_summary_found(self) -> None:
        result = at.dispatch_tool("get_validation_summary", {"round_index": 2}, make_ctx())
        assert result["best_val_wape"] == 0.31

    def test_get_validation_summary_missing_round(self) -> None:
        result = at.dispatch_tool("get_validation_summary", {"round_index": 99}, make_ctx())
        assert result["error"] == "ROUND_NOT_AVAILABLE"

    def test_submit_tuning_plan_accepted(self) -> None:
        ctx = make_ctx()
        plan = deterministic_fallback_plan(SNAPSHOT, ctx.frequency, "fixture")
        result = at.dispatch_tool("submit_tuning_plan", {"plan": plan}, ctx)
        assert result["accepted"] is True
        assert result["errors"] == []

    def test_submit_tuning_plan_rejected(self) -> None:
        ctx = make_ctx()
        plan = deterministic_fallback_plan(SNAPSHOT, ctx.frequency, "fixture")
        plan["max_rounds"] = 99
        result = at.dispatch_tool("submit_tuning_plan", {"plan": plan}, ctx)
        assert result["accepted"] is False
        assert any(e["code"] == "PLAN_BUDGET_VIOLATION" for e in result["errors"])

    def test_all_six_allowed_tools_registered(self) -> None:
        assert set(at.ALLOWED_TOOLS) == {
            "get_dataset_profile",
            "get_kg_model_candidates",
            "get_parameter_contracts",
            "get_experiment_history",
            "get_validation_summary",
            "submit_tuning_plan",
        }


class TestForbiddenTools:
    @pytest.mark.parametrize(
        "name",
        [
            "exec",
            "shell",
            "process",
            "read_file",
            "write_file",
            "postgres",
            "web_search",
            "web_fetch",
            "gateway",
            "cron",
            "approval",
            "promotion",
            "set_active_pointer",
        ],
    )
    def test_forbidden_tool_rejected(self, name: str) -> None:
        result = at.dispatch_tool(name, {}, make_ctx())
        assert result == {"error": "TOOL_NOT_PERMITTED", "tool": name}

    def test_unknown_tool_rejected(self) -> None:
        result = at.dispatch_tool("drop_all_tables", {}, make_ctx())
        assert result["error"] == "TOOL_NOT_PERMITTED"


class TestBuildQwenContext:
    def test_exact_whitelist_keys(self) -> None:
        context = at.build_qwen_context(make_ctx(), round_index=1)
        assert set(context.keys()) == {
            "track_key",
            "frequency",
            "snapshot_sha256",
            "round_index",
            "dataset_profile",
            "candidates",
            "budgets",
            "targets",
            "validation_history",
        }

    def test_budgets_and_targets(self) -> None:
        context = at.build_qwen_context(make_ctx(), round_index=2)
        assert context["budgets"] == {"max_rounds": 3, "max_trials_per_round": 50, "max_total_minutes": 45}
        assert context["targets"] == {
            "wape_max": 0.30,
            "agreement_pct_min": 70.0,
            "coverage_min": 0.85,
            "baseline_improvement_min": 0.01,
        }
        assert context["round_index"] == 2
        assert context["validation_history"] == make_ctx().validation_summaries

    def test_context_profile_matches_trainval_only(self) -> None:
        ctx = make_ctx()
        context = at.build_qwen_context(ctx, round_index=1)
        assert context["dataset_profile"] == at.dataset_profile(ctx.trainval_rows, ctx.frequency)

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import psycopg

NS = uuid.UUID("c87f8a95-12c2-5b4d-a3bf-cd24867f5d37")


def stable_uuid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{key}")


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


@dataclass(frozen=True)
class ModelSpec:
    name: str
    min_periods: int
    cadence: str
    supports_missing: bool
    description_cn: str


class ModelRegistry:
    def __init__(self) -> None:
        self.models = {
            "last_value_baseline": ModelSpec(
                "last_value_baseline", 1, "any", True, "使用截止日前最后一个真实观测值，适合极短或稀疏序列的保守基线。"
            ),
            "rolling_mean_baseline": ModelSpec(
                "rolling_mean_baseline", 4, "any", True, "使用最近若干个非缺失真实观测的平均值，适合短序列趋势演示。"
            ),
            "seasonal_naive_12_month": ModelSpec(
                "seasonal_naive_12_month", 24, "month", True, "使用去年同月作为季节性基线，适合具备两年及以上月度数据的病种。"
            ),
            "seasonal_naive_52_week": ModelSpec(
                "seasonal_naive_52_week", 104, "week", True, "使用去年同周作为季节性基线，要求至少两个完整周度季节循环。"
            ),
        }

    def executable(self) -> list[dict[str, Any]]:
        return [spec.__dict__ for spec in self.models.values()]

    def choose(self, profile: dict[str, Any]) -> tuple[ModelSpec, str]:
        n = int(profile.get("observed_non_null_period_count") or 0)
        cadence = str(profile.get("cadence") or "")
        if cadence == "month" and n >= 24:
            return self.models["seasonal_naive_12_month"], "MONTHLY_SEASONAL_BASELINE"
        if cadence == "week" and n >= 104:
            return self.models["seasonal_naive_52_week"], "WEEKLY_FORMAL_SEASONAL_BASELINE"
        if n >= 4:
            return self.models["rolling_mean_baseline"], "SHORT_SERIES_BASELINE_FALLBACK"
        return self.models["last_value_baseline"], "VERY_SHORT_SERIES_LAST_VALUE_FALLBACK"


class RecommendationProvider:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def approved_executable_kg_candidates(self, disease_slug: str, metric_code: str) -> list[dict[str, Any]]:
        # Only pre-existing human-reviewed, eligible KG claims may participate.
        # R4 machine candidates are explicitly excluded.
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select claim_id::text, canonical_key, subject_text, predicate_text, object_text,
                       metric_name, forecast_horizon, properties
                from epidemic_kg.kg_claims
                where evidence_eligible=true and quarantine=false and review_status in ('APPROVED','REVIEWED_APPROVED','HUMAN_REVIEWED_APPROVED','VALIDATED')
                  and coalesce(properties->>'paper1_participation','0') <> '1'
                  and (
                    lower(coalesce(subject_text,'')) like %s or
                    lower(coalesce(object_text,'')) like %s
                  )
                  and lower(coalesce(metric_name,'')) like %s
                limit 20
                """,
                (f"%{disease_slug.lower()}%", f"%{disease_slug.lower()}%", f"%{metric_code.lower()}%"),
            )
            return [
                dict(
                    zip(
                        [
                            "claim_id",
                            "canonical_key",
                            "subject_text",
                            "predicate_text",
                            "object_text",
                            "metric_name",
                            "forecast_horizon",
                            "properties",
                        ],
                        r,
                        strict=False,
                    )
                )
                for r in cur.fetchall()
            ]


class ParameterResolver:
    def resolve(self, model: ModelSpec, profile: dict[str, Any], kg_candidates: list[dict[str, Any]]) -> dict[str, Any]:
        if model.name == "rolling_mean_baseline":
            window = min(4, max(1, int(profile.get("observed_non_null_period_count") or 1)))
            return {"model_name": model.name, "params": {"window": window}, "source": "registered_model_default", "kg_candidate_count": len(kg_candidates)}
        if model.name == "seasonal_naive_12_month":
            return {"model_name": model.name, "params": {"season_lag": 12}, "source": "registered_model_default", "kg_candidate_count": len(kg_candidates)}
        if model.name == "seasonal_naive_52_week":
            return {"model_name": model.name, "params": {"season_lag": 52}, "source": "registered_model_default", "kg_candidate_count": len(kg_candidates)}
        return {"model_name": model.name, "params": {}, "source": "registered_model_default", "kg_candidate_count": len(kg_candidates)}


class ForecastOrchestrator:
    def __init__(self, conn: psycopg.Connection, token_prefix: str = "r4") -> None:
        self.conn = conn
        self.token_prefix = token_prefix
        self.registry = ModelRegistry()
        self.provider = RecommendationProvider(conn)
        self.resolver = ParameterResolver()

    def series(self, country: str, disease: str, cadence: str, metric: str) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select observation_id, period_start, period_end, value_numeric
                from epidemic.training_observations_v1
                where country_alpha3=%s and disease_slug=%s and granularity=%s and metric_code=%s
                  and value_numeric is not null
                order by period_start
                """,
                (country, disease, cadence, metric),
            )
            return [
                dict(zip(["observation_id", "period_start", "period_end", "value"], r, strict=False))
                for r in cur.fetchall()
            ]

    def profile(self, rows: list[dict[str, Any]], country: str, disease: str, cadence: str, metric: str) -> dict[str, Any]:
        return {
            "country_alpha3": country,
            "disease_slug": disease,
            "cadence": cadence,
            "metric_code": metric,
            "observed_non_null_period_count": len(rows),
            "start": str(rows[0]["period_start"]) if rows else None,
            "end": str(rows[-1]["period_start"]) if rows else None,
            "data_through": str(rows[-1]["period_start"]) if rows else None,
            "unique_period_count": len({r["period_start"] for r in rows}),
        }

    def _next_period(self, d: date, cadence: str) -> date:
        if cadence == "week":
            return d + timedelta(days=7)
        if cadence == "month":
            y, m = d.year + (1 if d.month == 12 else 0), 1 if d.month == 12 else d.month + 1
            return date(y, m, 1)
        return d

    def predict_value(self, history: list[dict[str, Any]], model: ModelSpec, params: dict[str, Any], target_index: int, horizon: int = 1) -> float | None:
        vals = [float(r["value"]) for r in history if r.get("value") is not None]
        if not vals:
            return None
        if model.name == "rolling_mean_baseline":
            w = int(params.get("window") or 4)
            return sum(vals[-w:]) / min(w, len(vals))
        if model.name == "seasonal_naive_12_month" and len(vals) >= 12:
            offset = 12 - ((max(1, horizon) - 1) % 12)
            return vals[-offset]
        if model.name == "seasonal_naive_52_week" and len(vals) >= 52:
            offset = 52 - ((max(1, horizon) - 1) % 52)
            return vals[-offset]
        return vals[-1]

    def candidate_models(self, profile: dict[str, Any]) -> list[tuple[ModelSpec, str]]:
        n = int(profile.get("observed_non_null_period_count") or 0)
        cadence = str(profile.get("cadence") or "")
        candidates: list[tuple[ModelSpec, str]] = []
        for spec in self.registry.models.values():
            if spec.cadence not in {"any", cadence}:
                continue
            if n < spec.min_periods:
                continue
            candidates.append((spec, "ADMITTED_BY_REGISTRY_HISTORY_AND_CADENCE"))
        if not candidates and n:
            candidates.append((self.registry.models["last_value_baseline"], "ADMITTED_AS_FAIL_CLOSED_BASELINE"))
        return candidates

    def backtest_candidate(self, rows: list[dict[str, Any]], model: ModelSpec, params: dict[str, Any]) -> dict[str, Any]:
        fold_count = min(8, max(0, len(rows) - max(1, model.min_periods)))
        start = len(rows) - fold_count
        errors: list[float] = []
        actual_sum = 0.0
        by_fold: list[dict[str, Any]] = []
        for i in range(start, len(rows)):
            if i <= 0:
                continue
            hist = rows[:i]
            target = rows[i]
            yhat = self.predict_value(hist, model, params, i, 1)
            if yhat is None or target.get("value") is None:
                continue
            actual = float(target["value"])
            err = abs(float(yhat) - actual)
            errors.append(err)
            actual_sum += abs(actual)
            by_fold.append(
                {
                    "origin": str(hist[-1]["period_start"]),
                    "target_period_start": str(target["period_start"]),
                    "horizon": 1,
                    "yhat": float(yhat),
                    "actual": actual,
                    "absolute_error": err,
                }
            )
        mae = sum(errors) / len(errors) if errors else None
        wape = sum(errors) / actual_sum if errors and actual_sum else None
        return {
            "model_name": model.name,
            "fold_count": len(errors),
            "metric_row_count": len(errors),
            "mae": mae,
            "wape": wape,
            "by_fold": by_fold,
        }

    def run(self, country: str, disease: str, cadence: str, metric: str, mode: str = "SHADOW") -> dict[str, Any]:
        rows = self.series(country, disease, cadence, metric)
        profile = self.profile(rows, country, disease, cadence, metric)
        if cadence not in {"week", "month"}:
            return self.persist_decision(country, disease, cadence, metric, profile, "NOT_APPLICABLE", "NOT_ELIGIBLE_FOR_CONTINUOUS_POINT_FORECAST", [])
        registry_model, registry_reason = self.registry.choose(profile)
        approved = self.provider.approved_executable_kg_candidates(disease, metric)
        candidates = self.candidate_models(profile)
        evaluated: list[dict[str, Any]] = []
        for spec, admission_reason in candidates:
            resolution_i = self.resolver.resolve(spec, profile, approved)
            metrics_i = self.backtest_candidate(rows, spec, resolution_i["params"])
            evaluated.append(
                {
                    "model_name": spec.name,
                    "admission_status": "ADMITTED",
                    "admission_reason": admission_reason,
                    "description_cn": spec.description_cn,
                    "parameter_resolution": resolution_i,
                    "backtest_metrics": {k: v for k, v in metrics_i.items() if k != "by_fold"},
                    "folds": metrics_i["by_fold"],
                }
            )
        rejected = []
        for spec in self.registry.models.values():
            if spec.name in {c["model_name"] for c in evaluated}:
                continue
            if spec.cadence not in {"any", cadence}:
                rejected.append({"model_name": spec.name, "rejection_reason": "CADENCE_MISMATCH"})
            elif int(profile.get("observed_non_null_period_count") or 0) < spec.min_periods:
                rejected.append({"model_name": spec.name, "rejection_reason": "INSUFFICIENT_HISTORY"})
        baseline = next((c for c in evaluated if c["model_name"] == "last_value_baseline"), None)
        comparable = [c for c in evaluated if c["backtest_metrics"].get("wape") is not None]
        best = min(comparable, key=lambda c: (c["backtest_metrics"]["wape"], c["model_name"])) if comparable else None
        if baseline and best and baseline["backtest_metrics"].get("wape") is not None:
            base_wape = float(baseline["backtest_metrics"]["wape"])
            best_wape = float(best["backtest_metrics"]["wape"])
            if best["model_name"] != baseline["model_name"] and base_wape > 0 and (base_wape - best_wape) / base_wape < 0.05:
                champion_name = baseline["model_name"]
                reason = "ML_NOT_BETTER_THAN_BASELINE"
            elif best["model_name"] != baseline["model_name"]:
                champion_name = best["model_name"]
                reason = "BACKTEST_BEATS_BASELINE_GUARD"
            else:
                champion_name = baseline["model_name"]
                reason = "BASELINE_CHAMPION_BY_BACKTEST"
        elif best:
            champion_name = best["model_name"]
            reason = "ONLY_MODEL_WITH_BACKTEST_METRICS"
        else:
            champion_name = registry_model.name
            reason = registry_reason
        model = self.registry.models[champion_name]
        resolution = self.resolver.resolve(model, profile, approved)
        fallback_reason = None if approved else "NO_APPROVED_EXECUTABLE_KG_CANDIDATE_USING_REGISTERED_BASELINE"
        idempotency_key = f"{self.token_prefix}:{country}:{disease}:{cadence}:{metric}:{mode}:{model.name}"
        run_id = stable_uuid("shadow-run", idempotency_key)
        run_key = idempotency_key
        data_through = rows[-1]["period_start"] if rows else None
        trace = {
            "recommendation_reason": reason,
            "kg_approved_candidate_count": len(approved),
            "candidate_models": evaluated,
            "rejected_models": rejected,
            "executed_candidate_count": len(evaluated),
            "baseline_guard": {
                "baseline_model": "last_value_baseline",
                "champion_model": model.name,
                "reason_code": reason,
            },
            "pending_machine_claim_participation_count": 0,
            "parameter_resolution": resolution,
            "fallback_reason": fallback_reason,
            "global_104_hard_block": False,
            "cdc_r4f_allowed": False,
            "run_mode_semantics": "DEMO_SHADOW_NON_PUBLISHED_NO_PROMOTION",
        }
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into epidemic_kg.kg_forecast_shadow_runs
                (run_id,run_key,idempotency_key,mode,status,disease_slug,country_alpha3,metric_code,cadence,data_through,model_name,fallback_reason,promotion_eligible,active_pointer_changed,trusted_rows_changed,profile,selection_trace)
                values(%s,%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,false,false,0,%s::jsonb,%s::jsonb)
                on conflict(idempotency_key) do update set updated_at=now(), profile=excluded.profile, selection_trace=excluded.selection_trace
                """,
                (run_id, run_key, idempotency_key, mode, disease, country, metric, cadence, data_through, model.name, fallback_reason, json.dumps(profile), json.dumps(trace)),
            )
            # Rolling-ish backtest on last up to 4 points, plus four future periods.
            generated = 0
            backtest_start = max(1, len(rows) - 4)
            for i in range(backtest_start, len(rows)):
                hist = rows[:i]
                target = rows[i]
                yhat = self.predict_value(hist, model, resolution["params"], i, 1)
                if yhat is None:
                    continue
                self._insert_value(cur, run_id, target["period_start"], target["period_end"], 1, yhat, target["value"], True, "BACKTEST", [target["observation_id"]])
                generated += 1
            future_start = rows[-1]["period_start"] if rows else date.today()
            hist = rows[:]
            for h in range(1, 5):
                target_start = self._next_period(future_start, cadence)
                future_start = target_start
                yhat = self.predict_value(hist, model, resolution["params"], len(hist) + h, h)
                if yhat is None:
                    continue
                self._insert_value(cur, run_id, target_start, target_start, h, yhat, None, False, "FUTURE", [])
                generated += 1
        return {"run_id": str(run_id), "run_key": run_key, "forecast_output_row_count": generated, "profile": profile, "trace": trace, "promotion_eligible": False, "active_pointer_changed": False}

    def persist_decision(
        self,
        country: str,
        disease: str,
        cadence: str,
        metric: str,
        profile: dict[str, Any],
        mode: str,
        status: str,
        outputs: list[Any],
    ) -> dict[str, Any]:
        idempotency_key = f"{self.token_prefix}:{country}:{disease}:{cadence}:{metric}:{mode}:decision"
        run_id = stable_uuid("shadow-run", idempotency_key)
        trace = {"decision": status, "continuous_series_fabricated": False, "global_104_hard_block": False}
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into epidemic_kg.kg_forecast_shadow_runs
                (run_id,run_key,idempotency_key,mode,status,disease_slug,country_alpha3,metric_code,cadence,model_name,promotion_eligible,active_pointer_changed,trusted_rows_changed,profile,selection_trace)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,'not_applicable_decision',false,false,0,%s::jsonb,%s::jsonb)
                on conflict(idempotency_key) do update set updated_at=now(), profile=excluded.profile, selection_trace=excluded.selection_trace
                """,
                (run_id, idempotency_key, idempotency_key, mode, status, disease, country, metric, cadence, json.dumps(profile), json.dumps(trace)),
            )
            value_id = stable_uuid("shadow-decision", idempotency_key)
            cur.execute(
                """
                insert into epidemic_kg.kg_forecast_shadow_values
                (value_id, run_id, horizon, row_type, is_observed, is_imputed, properties)
                values(%s,%s,0,'SCENARIO_DECISION',false,false,%s::jsonb)
                on conflict(value_id) do nothing
                """,
                (value_id, run_id, json.dumps(trace)),
            )
        return {"run_id": str(run_id), "forecast_output_row_count": 1, "profile": profile, "trace": trace, "promotion_eligible": False, "active_pointer_changed": False}

    def _insert_value(
        self,
        cur: Any,
        run_id: uuid.UUID,
        start: date,
        end: date,
        horizon: int,
        yhat: float,
        actual: Any,
        observed: bool,
        row_type: str,
        obs_ids: list[Any],
    ) -> None:
        lower = yhat if yhat is None else max(0.0, yhat * 0.8)
        upper = yhat if yhat is None else yhat * 1.2 + 1.0
        key = f"{run_id}:{start}:{horizon}:{row_type}"
        cur.execute(
            """
            insert into epidemic_kg.kg_forecast_shadow_values
            (value_id,run_id,target_period_start,target_period_end,horizon,yhat,lower_value,upper_value,actual_value,is_observed,is_imputed,imputation_method,source_observation_ids,row_type,properties)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,null,%s,%s,%s::jsonb)
            on conflict(run_id,target_period_start,horizon,row_type) do nothing
            """,
            (stable_uuid("shadow-value", key), run_id, start, end, horizon, yhat, lower, upper, actual, observed, obs_ids, row_type, json.dumps({"model_semantics": "baseline_no_training_no_promotion"})),
        )


DISEASE_RE = re.compile(r"\b(covid|sars-cov-2|influenza|flu|dengue|ebola|legionella|legionellosis|hantavirus|puumala|nipah|mpox|monkeypox)\b", re.I)
MODEL_RE = re.compile(r"\b(xgboost|lightgbm|random forest|lstm|gru|rnn|transformer|arima|sarima|seir|sir|agent[- ]based|bayesian|gaussian process|kalman|regression|baseline|neural network)\b", re.I)
PARAM_RE = re.compile(r"\b(horizon|lag|window|learning rate|depth|estimators|hidden units|layers|seasonal period|serial interval|incubation period|r0)\b(?:\\s*(?:=|of|:)?\\s*([0-9]+(?:\\.[0-9]+)?))?", re.I)


def extract_structured_candidate(text: str) -> dict[str, str] | None:
    disease = DISEASE_RE.search(text)
    model = MODEL_RE.search(text)
    if not model:
        return None
    param = PARAM_RE.search(text)
    if not disease and not param:
        return None
    return {
        "disease_text": disease.group(0) if disease else "",
        "model_text": model.group(0),
        "parameter_name": param.group(1) if param else "",
        "parameter_value": param.group(2) if param and param.lastindex and param.group(2) else "",
    }

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.safety import sanitize_mapping
from apps.api.schemas import MinimumDataRequest, ModelPromotionCheckRequest
from predictor_agent.minimum_data_analyzer import (
    MinimumDataAnalysisRequest,
    MinimumDataAnalyzer,
    result_to_dict,
)
from predictor_agent.model_governance import ModelEvaluationSummary, compare_model_metrics

router = APIRouter(tags=["analysis"])


@router.post("/analysis/minimum-data")
def minimum_data_analysis(request: MinimumDataRequest, session: Session = Depends(get_db_session)) -> dict:
    result = MinimumDataAnalyzer(session).analyze(
        MinimumDataAnalysisRequest(
            country=request.country,
            disease=request.disease,
            horizon_days=request.horizon_days,
            target_metric=request.target_metric,
            min_acceptable_score=request.min_acceptable_score,
            candidate_feature_groups=request.candidate_feature_groups,
            history_windows_weeks=request.history_windows_weeks,
            model_name=request.model_name,
            min_train_size=request.min_train_size,
        )
    )
    payload = result_to_dict(result)
    evaluations = payload.pop("evaluations", []) or []
    payload["evaluation_count"] = len(evaluations)
    payload["evaluation_preview"] = sanitize_mapping(evaluations[:20], max_items=20)
    payload["limitations"] = "Current historical data and rolling backtest settings only; future accuracy is not guaranteed."
    payload["auto_retrain_triggered"] = False
    payload["auto_model_replacement_triggered"] = False
    return payload


@router.post("/analysis/model-promotion-check")
def model_promotion_check(request: ModelPromotionCheckRequest) -> dict:
    decision = compare_model_metrics(
        champion=ModelEvaluationSummary(
            model_version=request.champion.model_version,
            model_name=request.champion.model_name,
            metrics=request.champion.metrics,
            warnings=request.champion.warnings,
        ),
        challenger=ModelEvaluationSummary(
            model_version=request.challenger.model_version,
            model_name=request.challenger.model_name,
            metrics=request.challenger.metrics,
            warnings=request.challenger.warnings,
        ),
        primary_metric=request.primary_metric,
        lower_is_better=request.lower_is_better,
        min_relative_improvement=request.min_relative_improvement,
        max_false_alarm_regression=request.max_false_alarm_regression,
        max_missed_outbreak_regression=request.max_missed_outbreak_regression,
    )
    return {
        "promote": decision.promote,
        "reason": decision.reason,
        "champion_version": decision.champion_version,
        "challenger_version": decision.challenger_version,
        "checks": sanitize_mapping(decision.checks, max_items=30),
        "limitations": (
            "Offline promotion check only. It does not retrain, deploy, or replace a production model; "
            "analyst review is required."
        ),
        "auto_retrain_triggered": False,
        "auto_model_replacement_triggered": False,
    }

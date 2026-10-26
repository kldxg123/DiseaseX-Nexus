from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import Prediction, utc_now


class PredictionStore:
    def __init__(self, session: Session):
        self.session = session

    def save_predictions(self, rows: list[dict], *, replace_existing: bool = True) -> dict:
        summary = {
            "rows_seen": len(rows),
            "saved": 0,
            "updated": 0,
            "skipped": 0,
        }
        for row in rows:
            existing = self._find_existing(row)
            if existing is not None:
                if not replace_existing:
                    summary["skipped"] += 1
                    continue
                self._apply_row(existing, row)
                existing.updated_at = utc_now()
                summary["updated"] += 1
                continue
            prediction = Prediction(
                country=row["country"],
                admin1=row.get("admin1"),
                disease=row["disease"],
                target_date=_as_date(row["target_date"]),
                iso_week=row["iso_week"],
                horizon_days=int(row["horizon_days"]),
                model_name=row["model_name"],
                model_version=row["model_version"],
                predicted_value=0.0,
                lower_bound=0.0,
                upper_bound=0.0,
                uncertainty_method=row["uncertainty_method"],
                prediction_interval_json={},
                metrics_json={},
                provenance_json={},
                review_status="pending",
            )
            self._apply_row(prediction, row)
            self.session.add(prediction)
            summary["saved"] += 1
        self.session.flush()
        return summary

    def count_predictions(self, country: str | None = None, disease: str | None = None) -> int:
        statement = select(Prediction)
        if country is not None:
            statement = statement.where(Prediction.country == country)
        if disease is not None:
            statement = statement.where(Prediction.disease == disease)
        return len(self.session.scalars(statement).all())

    def save_prediction(
        self,
        *,
        feature_record: Any,
        output: Any,
        model_version: str,
        risk_level: str | None = None,
        replace_existing: bool = True,
    ) -> dict:
        """Persist one stable predictor output without exposing raw features."""
        probability = float(getattr(output, "predicted_outbreak_probability", 0.0))
        resolved_risk = risk_level or classify_risk_level(probability)
        explanation = dict(getattr(output, "explanation", None) or {})
        model_name = str(explanation.get("model_name") or "baseline")
        predicted_cases = float(getattr(output, "predicted_cases"))
        lower_bound = float(getattr(output, "lower_bound"))
        upper_bound = float(getattr(output, "upper_bound"))
        uncertainty_method = str(explanation.get("uncertainty_method") or "baseline_interval")
        row = {
            "country": feature_record.country,
            "admin1": feature_record.admin1,
            "disease": feature_record.disease,
            "target_date": feature_record.target_date,
            "iso_week": feature_record.iso_week,
            "horizon_days": feature_record.horizon_days,
            "model_name": model_name,
            "model_version": model_version,
            "predicted_value": predicted_cases,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "uncertainty_method": uncertainty_method,
            "prediction_interval_json": {
                "level": 0.95,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
            },
            "metrics_json": {
                "predicted_outbreak_probability": probability,
                "risk_level": resolved_risk,
            },
            "provenance_json": {
                "feature_record_id": getattr(feature_record, "id", None),
                "source": "model_features",
                "model_name": model_name,
                "model_version": model_version,
                "predicted_outbreak_probability": probability,
                "risk_level": resolved_risk,
                "explanation": _safe_explanation(explanation),
                "human_review_required": True,
            },
            "review_status": "pending",
        }
        return self.save_predictions([row], replace_existing=replace_existing)

    def _find_existing(self, row: dict) -> Prediction | None:
        statement = select(Prediction).where(
            Prediction.country == row["country"],
            Prediction.disease == row["disease"],
            Prediction.iso_week == row["iso_week"],
            Prediction.horizon_days == int(row["horizon_days"]),
            Prediction.model_name == row["model_name"],
            Prediction.model_version == row["model_version"],
        )
        admin1 = row.get("admin1")
        if admin1 is None:
            statement = statement.where(Prediction.admin1.is_(None))
        else:
            statement = statement.where(Prediction.admin1 == admin1)
        return self.session.scalar(statement)

    def _apply_row(self, prediction: Prediction, row: dict) -> None:
        prediction.target_date = _as_date(row["target_date"])
        prediction.predicted_value = float(row["predicted_value"])
        prediction.lower_bound = float(row["lower_bound"])
        prediction.upper_bound = float(row["upper_bound"])
        prediction.uncertainty_method = str(row["uncertainty_method"])
        prediction.prediction_interval_json = _json_safe_dict(row.get("prediction_interval_json") or {})
        prediction.metrics_json = _json_safe_dict(row.get("metrics_json") or {})
        prediction.provenance_json = _json_safe_dict(row.get("provenance_json") or {})
        prediction.review_status = str(row.get("review_status") or "pending")


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError("target_date must be a date or ISO date string.")


def _json_safe_dict(value: dict) -> dict:
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            safe[str(key)] = _json_safe_dict(item)
        elif isinstance(item, list):
            safe[str(key)] = [
                subitem if isinstance(subitem, (str, int, float, bool)) or subitem is None else str(subitem)
                for subitem in item
            ]
        elif isinstance(item, (str, int, float, bool)) or item is None:
            safe[str(key)] = item
        elif isinstance(item, date):
            safe[str(key)] = item.isoformat()
        else:
            safe[str(key)] = str(item)
    return safe


def classify_risk_level(probability: float) -> str:
    value = max(0.0, min(1.0, float(probability)))
    if value >= 0.75:
        return "critical"
    if value >= 0.5:
        return "high"
    if value >= 0.25:
        return "medium"
    return "low"


def _safe_explanation(explanation: dict) -> dict:
    allowed_keys = {
        "model_name",
        "fallback_used",
        "input_feature_keys",
        "training_rows",
        "uncertainty_method",
        "residual_std",
    }
    return {key: value for key, value in _json_safe_dict(explanation).items() if key in allowed_keys}

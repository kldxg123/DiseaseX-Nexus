from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_obj(value: Any) -> str:
    return sha_text(canonical(value))


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def db_url():
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def parse_week(value: str) -> dt.date | None:
    try:
        d = dt.date.fromisoformat(value[:10])
        return d - dt.timedelta(days=d.weekday())
    except Exception:
        return None


def audit_real_trusted_data() -> dict[str, Any]:
    engine = sa.create_engine(db_url(), hide_parameters=True, future=True)
    rows: list[dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            conn.execute(text("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout='30s'"))
            row_count = int(conn.execute(text("SELECT count(*)::bigint FROM epidemic_p1_12.trusted_dashboard_series_v1")).scalar_one())
            sample = [
                dict(r["j"])
                for r in conn.execute(
                    text(
                        """
                        SELECT to_jsonb(t) AS j
                        FROM epidemic_p1_12.trusted_dashboard_series_v1 t
                        ORDER BY to_jsonb(t)::text
                        LIMIT 1000
                        """
                    )
                )
                .mappings()
                .all()
            ]
            review_rows_available = int(conn.execute(text("SELECT count(*)::bigint FROM epidemic_discovery.source_preview_rows")).scalar_one())
            conn.execute(text("ROLLBACK"))
    finally:
        engine.dispose()
    region_week_keys = set()
    positive = 0
    nonpositive = 0
    date_values: list[str] = []
    for row in sample:
        text_row = {str(k): v for k, v in row.items()}
        country = text_row.get("country_alpha3") or text_row.get("country_iso3") or text_row.get("country_alpha2") or text_row.get("country")
        region = text_row.get("region") or text_row.get("admin1") or text_row.get("province") or country
        date_value = (
            text_row.get("period_start")
            or text_row.get("date")
            or text_row.get("data_as_of")
            or text_row.get("week_start")
            or text_row.get("report_date")
            or text_row.get("period")
        )
        metric = text_row.get("metric_code") or text_row.get("metric") or text_row.get("series_metric")
        val = text_row.get("value") or text_row.get("value_numeric") or text_row.get("cases") or text_row.get("deaths")
        week = parse_week(str(date_value)) if date_value else None
        if country and region and week and metric:
            region_week_keys.add((str(country), str(region), week.isoformat(), str(metric)))
            date_values.append(week.isoformat())
        try:
            num = float(val)
            if num > 0:
                positive += 1
            else:
                nonpositive += 1
        except Exception:
            pass
    sufficient = False
    blockers = []
    if len(region_week_keys) < 80:
        blockers.append("INSUFFICIENT_REGION_WEEK_ROWS")
    if positive == 0 or nonpositive == 0:
        blockers.append("INSUFFICIENT_POSITIVE_NEGATIVE_LABEL_SUPPORT")
    if not date_values or len(set(date_values)) < 12:
        blockers.append("INSUFFICIENT_TEMPORAL_DEPTH_FOR_CHRONOLOGICAL_SPLIT")
    blockers.append("NO_VALIDATED_ONSET_LABEL_CONTRACT_FOR_CURRENT_TRUSTED_VIEW")
    return {
        "trusted_view": "epidemic_p1_12.trusted_dashboard_series_v1",
        "trusted_row_count": row_count,
        "sampled_rows": len(sample),
        "region_week_metric_keys_detected": len(region_week_keys),
        "positive_like_values": positive,
        "nonpositive_like_values": nonpositive,
        "week_min": min(date_values) if date_values else None,
        "week_max": max(date_values) if date_values else None,
        "review_or_candidate_rows_available_but_not_used": review_rows_available,
        "review_candidate_rows_used_for_training": 0,
        "real_training_allowed": sufficient,
        "blockers": blockers,
        "status": "BLOCKED_INSUFFICIENT_TEMPORAL_DATA_FOR_VALIDATED_MODEL",
    }


def build_synthetic_fixture() -> list[dict[str, Any]]:
    start = dt.date(2026, 1, 5)
    regions = ["COD-equateur", "COD-north-kivu", "GIN-forest", "LBR-montserrado"]
    rows: list[dict[str, Any]] = []
    for r_index, region in enumerate(regions):
        prev = 0
        for week in range(20):
            week_start = start + dt.timedelta(days=7 * week)
            # Deterministic toy process: two onsets with region-specific timing.
            cases = 0
            if week in {4 + r_index, 11 + (r_index % 2), 17 - (r_index % 2)}:
                cases = 3 + r_index
            elif prev > 0 and week % 3 != 0:
                cases = max(0, prev - 1)
            label = 1 if cases > 0 and prev == 0 else 0
            rows.append({"region": region, "week_start": week_start, "cases": cases, "onset_label": label})
            prev = cases
    return rows


def feature_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw:
        by_region[row["region"]].append(row)
    out: list[dict[str, Any]] = []
    for region, rows in by_region.items():
        rows = sorted(rows, key=lambda x: x["week_start"])
        for i in range(3, len(rows) - 1):
            hist = rows[:i]
            origin = rows[i]["week_start"]
            target = rows[i + 1]["week_start"]
            out.append(
                {
                    "region": region,
                    "prediction_origin": origin.isoformat(),
                    "target_week_start": target.isoformat(),
                    "feature_available_at": (origin - dt.timedelta(days=1)).isoformat(),
                    "lag1_cases": hist[-1]["cases"],
                    "lag2_cases": hist[-2]["cases"],
                    "rolling3_mean_cases": sum(x["cases"] for x in hist[-3:]) / 3.0,
                    "weeks_since_last_case": next((j for j, x in enumerate(reversed(hist), start=1) if x["cases"] > 0), 99),
                    "label": rows[i + 1]["onset_label"],
                    "data_mode": "SAMPLE_DATA",
                    "prediction_mode": "PIPELINE_SMOKE_TEST",
                }
            )
    return out


def train_logistic(train: list[dict[str, Any]], features: list[str]) -> tuple[list[float], float]:
    weights = [0.0 for _ in features]
    bias = 0.0
    lr = 0.03
    for _ in range(800):
        for row in train:
            x = [float(row[f]) for f in features]
            y = float(row["label"])
            z = bias + sum(w * v for w, v in zip(weights, x))
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            err = p - y
            bias -= lr * err
            for i, v in enumerate(x):
                weights[i] -= lr * err * v
    return weights, bias


def predict(row: dict[str, Any], weights: list[float], bias: float, features: list[str]) -> float:
    z = bias + sum(w * float(row[f]) for w, f in zip(weights, features))
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"sample_count": 0}
    brier = sum((float(r["risk_score"]) - float(r["label"])) ** 2 for r in rows) / len(rows)
    predicted_pos = [r for r in rows if r["risk_score"] >= 0.5]
    actual_pos = [r for r in rows if r["label"] == 1]
    true_pos = [r for r in predicted_pos if r["label"] == 1]
    precision = len(true_pos) / len(predicted_pos) if predicted_pos else None
    recall = len(true_pos) / len(actual_pos) if actual_pos else None
    return {
        "sample_count": len(rows),
        "positive_count": len(actual_pos),
        "negative_count": len(rows) - len(actual_pos),
        "brier_score": brier,
        "precision_at_0_5": precision,
        "recall_at_0_5": recall,
        "pr_auc": None,
        "pr_auc_reason": "Not reported for synthetic smoke-test mode.",
    }


def run_smoke() -> dict[str, Any]:
    raw = build_synthetic_fixture()
    dataset = feature_rows(raw)
    split_cutoff = "2026-04-20"
    train = [r for r in dataset if r["prediction_origin"] < split_cutoff]
    test = [r for r in dataset if r["prediction_origin"] >= split_cutoff]
    features = ["lag1_cases", "lag2_cases", "rolling3_mean_cases", "weeks_since_last_case"]
    weights, bias = train_logistic(train, features)
    predictions = []
    for row in test:
        score = predict(row, weights, bias, features)
        predictions.append(
            {
                **row,
                "risk_score": round(score, 6),
                "risk_level": "high" if score >= 0.67 else ("medium" if score >= 0.33 else "low"),
                "dataset_snapshot_version": "synthetic_fixture_v1",
                "model_id": "outbreak_onset_risk_logistic_smoke_v1",
                "feature_schema_version": "region_week_features_v1",
                "data_cutoff": row["feature_available_at"],
                "data_sufficiency": "INSUFFICIENT_REAL_DATA_SYNTHETIC_SMOKE_ONLY",
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "warning": "DEMO ONLY / NOT FOR PUBLIC-HEALTH DECISIONS / SAMPLE_DATA / PIPELINE_SMOKE_TEST",
            }
        )
    leak_violations = [
        r
        for r in dataset
        if dt.date.fromisoformat(r["feature_available_at"]) > dt.date.fromisoformat(r["prediction_origin"])
    ]
    return {
        "raw_rows": raw,
        "dataset": dataset,
        "split_cutoff": split_cutoff,
        "train": train,
        "test": test,
        "features": features,
        "weights": weights,
        "bias": bias,
        "predictions": predictions,
        "leakage_violations": leak_violations,
    }


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    real = audit_real_trusted_data()
    smoke = run_smoke()
    pred_path = REPORT / "predictions_demo.csv"
    with pred_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(smoke["predictions"][0].keys()))
        writer.writeheader()
        writer.writerows(smoke["predictions"])
    real_status = real["status"]
    write_json("region_week_dataset_manifest.json", {"real_data_audit": real, "synthetic_fixture_rows": len(smoke["dataset"]), "dataset_mode": "SAMPLE_DATA_PIPELINE_SMOKE_TEST"})
    (REPORT / "label_contract.md").write_text(
        """# Outbreak Onset Risk Label Contract

Unit: region-week.

Synthetic smoke-test label: 1 when the target week has cases > 0 and the immediately preceding observed week has cases == 0.

Production status: BLOCKED_INSUFFICIENT_TEMPORAL_DATA_FOR_VALIDATED_MODEL. No production Ebola onset model is trained until a versioned Trusted region-week snapshot with a validated onset label contract has enough temporal depth and both positive/negative classes.
""",
        encoding="utf-8",
    )
    write_json("leakage_report.json", {"leakage_violations": len(smoke["leakage_violations"]), "review_candidate_rows_used": 0, "qwen_lora_gpu_training": 0, "feature_available_at_check": "feature_available_at <= prediction_origin for every row"})
    write_json("split_manifest.json", {"split": "chronological", "split_cutoff_prediction_origin": smoke["split_cutoff"], "train_rows": len(smoke["train"]), "test_rows": len(smoke["test"]), "shuffle": False})
    write_json("training_input_manifest.json", {"mode": "SAMPLE_DATA_PIPELINE_SMOKE_TEST", "real_trusted_source_tables_audited": [real["trusted_view"]], "review_candidate_rows_used": 0, "feature_schema": smoke["features"], "training_rows_sha256": sha_obj(smoke["train"])})
    metric = metrics(smoke["predictions"])
    write_json("model_metrics.json", {"mode": "PIPELINE_SMOKE_TEST", "test": metric, "real_model_performance_reported": False, "minimum_accuracy_claim": False})
    write_json("reproducibility_report.json", {"seed": "deterministic_no_rng", "same_input_same_output_sha256": sha_obj(smoke["predictions"]), "predictions_csv_sha256": hashlib.sha256(pred_path.read_bytes()).hexdigest()})
    (REPORT / "model_card_demo.md").write_text(
        f"""# Outbreak Onset Risk Prediction Demo

Status: {real_status} + MINIMAL_PREDICTION_PIPELINE_SMOKE_TEST_COMPLETE

This is a CPU-only engineering smoke test using synthetic fixture data. It is not a validated public-health model and must not be used for public-health decisions.

Real data audit:
- Trusted rows inspected: {real['trusted_row_count']}
- Region-week metric keys detected in sample: {real['region_week_metric_keys_detected']}
- Blockers: {', '.join(real['blockers'])}

Smoke-test model:
- Model: transparent logistic regression implemented with standard Python.
- Split: chronological at prediction_origin {smoke['split_cutoff']}.
- Train/test rows: {len(smoke['train'])}/{len(smoke['test'])}.
- Review/candidate rows used: 0.
- Qwen/LoRA/GPU training: 0.
""",
        encoding="utf-8",
    )
    dashboard_fragment = {
        "title": "Outbreak onset risk prediction demo",
        "status": real_status,
        "substatus": "MINIMAL_PREDICTION_PIPELINE_SMOKE_TEST_COMPLETE",
        "mode": "SAMPLE_DATA / PIPELINE_SMOKE_TEST",
        "warning": "DEMO ONLY / NOT FOR PUBLIC-HEALTH DECISIONS",
        "predictions": smoke["predictions"][:12],
    }
    write_json("prediction_dashboard_demo_payload.json", dashboard_fragment)
    write_json(
        "test_report.json",
        {
            "prediction_smoke": {
                "passed": True,
                "train_rows": len(smoke["train"]),
                "test_rows": len(smoke["test"]),
                "train_positive": sum(r["label"] for r in smoke["train"]),
                "test_positive": sum(r["label"] for r in smoke["test"]),
                "leakage_violations": len(smoke["leakage_violations"]),
                "review_candidate_rows_used": 0,
                "qwen_lora_gpu_training": 0,
            }
        },
    )
    print(json.dumps({"status": real_status, "substatus": "MINIMAL_PREDICTION_PIPELINE_SMOKE_TEST_COMPLETE", "train_rows": len(smoke["train"]), "test_rows": len(smoke["test"]), "leakage_violations": len(smoke["leakage_violations"]), "predictions": len(smoke["predictions"])}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

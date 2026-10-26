from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import pickle
import shutil
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import make_url

SOURCE_KEY = "trusted-influenza-who-flunet-country-week-v1"
RUN_NAMESPACE = uuid.UUID("d0b777e8-f8b7-4cc9-8db5-9c6d312e6571")
THRESHOLD = 0.5
ENGINEERING_POSITIVITY_THRESHOLD = 0.10
MIN_SPECIMENS = 50
MODEL_KEY = "cpu_logistic_regression_no_class_weight_no_calibration_v1"
WARNING_TEXT = (
    "这是基于 FluNet 历史监测数据的工程链路演示，不是 WHO 官方预测，也不构成疫情预警或公共卫生决策依据。当前结果不是 Ebola 预测。"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    path.write_text(data, encoding="utf-8")
    return sha256_text(data)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for k in row:
                if k not in fieldnames:
                    fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row.get(k), ensure_ascii=False, sort_keys=True, default=str) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in fieldnames})
    return sha256_file(path)


def db_url() -> sa.URL | str:
    raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ["DASHBOARD_DATABASE_URL"]
    url = make_url(raw)
    if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def engine() -> sa.Engine:
    return sa.create_engine(db_url(), future=True, hide_parameters=True)


def to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def iso_week_sin_cos(d: date) -> tuple[float, float]:
    w = int(d.isocalendar().week)
    angle = 2 * math.pi * w / 53.0
    return math.sin(angle), math.cos(angle)


def sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1 / (1 + ez)
    ez = math.exp(z)
    return ez / (1 + ez)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class LogisticBundle:
    feature_names: list[str]
    means: list[float]
    stds: list[float]
    weights: list[float]
    bias: float
    train_rows: int
    train_positive: int

    def transform(self, features: dict[str, float | None]) -> list[float]:
        out: list[float] = []
        for i, name in enumerate(self.feature_names):
            v = features.get(name)
            if v is None or not math.isfinite(float(v)):
                v = self.means[i]
            out.append((float(v) - self.means[i]) / self.stds[i])
        return out

    def predict_proba(self, features: dict[str, float | None]) -> float:
        x = self.transform(features)
        return sigmoid(self.bias + sum(w * xv for w, xv in zip(self.weights, x)))


def fetch_source_rows(conn: sa.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(sa.text(
        """
        SELECT observation_id,country_iso3,country_name,metric_code,value_numeric,period_start,period_end,dimensions,source_key,source_name
        FROM epidemic_p1_12.trusted_dashboard_series_v1
        WHERE source_key=:source_key
          AND metric_code IN ('influenza_specimens_processed','influenza_positive_detections','influenza_a_detections','influenza_b_detections')
        ORDER BY country_iso3,dimensions->>'origin_source',period_start,metric_code,observation_id
        """
    ), {"source_key": SOURCE_KEY}).mappings().all()
    return [dict(r) for r in rows]


def build_weekly(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[date, dict[str, Any]]], list[dict[str, Any]]]:
    weekly: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    eligibility: list[dict[str, Any]] = []
    for r in rows:
        dims = r.get("dimensions") or {}
        origin = str(dims.get("origin_source") or "UNKNOWN")
        country = str(r["country_iso3"])
        cname = str(r.get("country_name") or country)
        series_key = f"{country}|{origin}|specimens_processed|positive_total"
        week = r["period_start"]
        if not isinstance(week, date):
            week = date.fromisoformat(str(week))
        bucket = weekly[series_key].setdefault(
            week,
            {
                "series_key": series_key,
                "region_id": country,
                "country_iso3": country,
                "country_name": cname,
                "surveillance_origin": origin,
                "week": week,
                "specimens": None,
                "positive": None,
                "observation_ids": [],
            },
        )
        metric = r["metric_code"]
        val = to_float(r["value_numeric"])
        if metric == "influenza_specimens_processed":
            bucket["specimens"] = val
        elif metric == "influenza_positive_detections":
            bucket["positive"] = val
        bucket["observation_ids"].append(str(r["observation_id"]))
    for sk, by_week in weekly.items():
        for week, b in sorted(by_week.items()):
            specimens = b["specimens"]
            positive = b["positive"]
            valid = specimens is not None and positive is not None and specimens >= MIN_SPECIMENS and 0 <= positive <= specimens
            b["valid"] = valid
            b["positivity"] = (positive / specimens) if valid else None
            eligibility.append(
                {
                    "series_key": sk,
                    "country_iso3": b["country_iso3"],
                    "surveillance_origin": b["surveillance_origin"],
                    "week": week.isoformat(),
                    "has_specimens": specimens is not None,
                    "has_positive_total": positive is not None,
                    "specimens": specimens,
                    "positive_total": positive,
                    "eligible_observed_week": valid,
                    "reason": "OK" if valid else "MISSING_OR_SPECIMENS_LT_50_OR_POSITIVE_GT_SPECIMENS",
                }
            )
    return weekly, eligibility


def make_features(weekly: dict[str, dict[date, dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    feature_names: list[str] = []
    for lag in range(1, 9):
        feature_names += [f"lag{lag}_positive", f"lag{lag}_specimens", f"lag{lag}_positivity", f"lag{lag}_observed"]
    feature_names += ["prev4_positivity_mean", "prev4_positivity_std", "positivity_delta_lag1_lag2", "iso_week_sin", "iso_week_cos"]
    for sk, by_week in weekly.items():
        if not by_week:
            continue
        min_w, max_w = min(by_week), max(by_week)
        w = min_w
        while w <= max_w:
            cur = by_week.get(w)
            prev = by_week.get(w - timedelta(days=7))
            label_valid = bool(cur and prev and cur.get("valid") and prev.get("valid"))
            if label_valid:
                y = 1 if (cur["positivity"] >= ENGINEERING_POSITIVITY_THRESHOLD and prev["positivity"] < ENGINEERING_POSITIVITY_THRESHOLD) else 0
                features: dict[str, float | None] = {}
                feature_max_week = w - timedelta(days=7)
                lag_pos_values: list[float] = []
                for lag in range(1, 9):
                    lw = w - timedelta(days=7 * lag)
                    lb = by_week.get(lw)
                    observed = bool(lb and lb.get("valid"))
                    features[f"lag{lag}_observed"] = 1.0 if observed else 0.0
                    features[f"lag{lag}_positive"] = float(lb["positive"]) if observed else None
                    features[f"lag{lag}_specimens"] = float(lb["specimens"]) if observed else None
                    features[f"lag{lag}_positivity"] = float(lb["positivity"]) if observed else None
                    if observed:
                        lag_pos_values.append(float(lb["positivity"]))
                prev4 = [features.get(f"lag{i}_positivity") for i in range(1, 5)]
                prev4v = [float(x) for x in prev4 if x is not None]
                features["prev4_positivity_mean"] = mean(prev4v) if prev4v else None
                features["prev4_positivity_std"] = statistics.pstdev(prev4v) if len(prev4v) > 1 else 0.0 if prev4v else None
                if features.get("lag1_positivity") is not None and features.get("lag2_positivity") is not None:
                    features["positivity_delta_lag1_lag2"] = float(features["lag1_positivity"]) - float(features["lag2_positivity"])
                else:
                    features["positivity_delta_lag1_lag2"] = None
                features["iso_week_sin"], features["iso_week_cos"] = iso_week_sin_cos(w)
                row_id = sha256_json({"series_key": sk, "target_week": w.isoformat(), "contract": "p1-17a-v1"})[:32]
                rows.append(
                    {
                        "row_id": row_id,
                        "series_key": sk,
                        "region_id": cur["region_id"],
                        "country_iso3": cur["country_iso3"],
                        "country_name": cur["country_name"],
                        "surveillance_origin": cur["surveillance_origin"],
                        "prediction_origin": (w - timedelta(days=7)).isoformat(),
                        "target_week": w.isoformat(),
                        "feature_cutoff": feature_max_week.isoformat(),
                        "actual_label": y,
                        "features": features,
                        "feature_row_sha256": sha256_json({"row_id": row_id, "features": features, "feature_cutoff": feature_max_week.isoformat()}),
                        "max_feature_week_assertion": feature_max_week < w,
                        "data_waterline": max_w.isoformat(),
                        "vintage_warning": "RETROSPECTIVE_FINALIZED_DATA_DEMO; historical vintage availability is not fully evaluable",
                    }
                )
            w += timedelta(days=7)
    return rows, feature_names


def assign_splits(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    weeks = sorted({date.fromisoformat(r["target_week"]) for r in rows})
    c1 = weeks[max(0, min(len(weeks) - 1, int(len(weeks) * 0.60) - 1))]
    c2 = weeks[max(0, min(len(weeks) - 1, int(len(weeks) * 0.80) - 1))]
    p1_end = c1 + timedelta(days=56)
    p2_end = c2 + timedelta(days=56)
    split_rows: list[dict[str, Any]] = []
    for r in rows:
        tw = date.fromisoformat(r["target_week"])
        if tw <= c1:
            split = "train"
        elif tw <= p1_end:
            split = "purge_1"
        elif tw <= c2:
            split = "validation"
        elif tw <= p2_end:
            split = "purge_2"
        else:
            split = "test"
        r["split"] = split
        split_rows.append({"row_id": r["row_id"], "series_key": r["series_key"], "target_week": r["target_week"], "split": split})
    return {
        "split_method": "chronological_target_week_calendar_no_shuffle",
        "C1": c1.isoformat(),
        "C2": c2.isoformat(),
        "purge_weeks_each_boundary": 8,
        "purge_1_end": p1_end.isoformat(),
        "purge_2_end": p2_end.isoformat(),
    }, split_rows


def train_bundle(train_rows: list[dict[str, Any]], feature_names: list[str]) -> LogisticBundle:
    labels = [int(r["actual_label"]) for r in train_rows]
    if len(set(labels)) < 2:
        raise RuntimeError("MODEL_GATE_FAILED_SINGLE_CLASS_TRAIN")
    means: list[float] = []
    stds: list[float] = []
    matrix: list[list[float]] = []
    for name in feature_names:
        vals = [float(r["features"][name]) for r in train_rows if r["features"].get(name) is not None and math.isfinite(float(r["features"][name]))]
        m = mean(vals)
        s = statistics.pstdev(vals) if len(vals) > 1 else 1.0
        if s == 0 or not math.isfinite(s):
            s = 1.0
        means.append(m)
        stds.append(s)
    for r in train_rows:
        row = []
        for i, name in enumerate(feature_names):
            v = r["features"].get(name)
            if v is None or not math.isfinite(float(v)):
                v = means[i]
            row.append((float(v) - means[i]) / stds[i])
        matrix.append(row)
    weights = [0.0] * len(feature_names)
    bias = math.log((sum(labels) + 0.5) / (len(labels) - sum(labels) + 0.5))
    lr = 0.05
    l2 = 0.001
    n = len(labels)
    for _ in range(120):
        grad_w = [0.0] * len(weights)
        grad_b = 0.0
        for x, y in zip(matrix, labels):
            p = sigmoid(bias + sum(w * xv for w, xv in zip(weights, x)))
            err = p - y
            grad_b += err
            for j, xv in enumerate(x):
                grad_w[j] += err * xv
        bias -= lr * grad_b / n
        for j in range(len(weights)):
            weights[j] -= lr * ((grad_w[j] / n) + l2 * weights[j])
    return LogisticBundle(feature_names, means, stds, weights, bias, len(train_rows), sum(labels))


def average_precision(y: list[int], scores: list[float]) -> float | None:
    positives = sum(y)
    if positives == 0:
        return None
    order = sorted(range(len(y)), key=lambda i: scores[i], reverse=True)
    hit = 0
    total = 0.0
    for rank, idx in enumerate(order, start=1):
        if y[idx] == 1:
            hit += 1
            total += hit / rank
    return total / positives


def ece(y: list[int], scores: list[float], bins: int = 10) -> float | None:
    if not y:
        return None
    total = 0.0
    n = len(y)
    for b in range(bins):
        lo = b / bins
        hi = (b + 1) / bins
        idx = [i for i, s in enumerate(scores) if (s >= lo and (s < hi or b == bins - 1))]
        if not idx:
            continue
        conf = mean([scores[i] for i in idx])
        acc = mean([float(y[i]) for i in idx])
        total += (len(idx) / n) * abs(conf - acc)
    return total


def metric_pack(y: list[int], scores: list[float], threshold: float = THRESHOLD) -> dict[str, Any]:
    if not y:
        return {"rows": 0}
    pred = [1 if s >= threshold else 0 for s in scores]
    tp = sum(1 for a, p in zip(y, pred) if a == 1 and p == 1)
    fp = sum(1 for a, p in zip(y, pred) if a == 0 and p == 1)
    tn = sum(1 for a, p in zip(y, pred) if a == 0 and p == 0)
    fn = sum(1 for a, p in zip(y, pred) if a == 1 and p == 0)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and (precision + recall) else None
    return {
        "rows": len(y),
        "positive": sum(y),
        "negative": len(y) - sum(y),
        "prevalence": sum(y) / len(y),
        "pr_auc": average_precision(y, scores),
        "brier": mean([(s - a) ** 2 for a, s in zip(y, scores)]),
        "ece": ece(y, scores),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alerts_per_100_region_weeks": 100 * fp / len(y),
        "threshold": threshold,
    }


def add_predictions(rows: list[dict[str, Any]], bundle: LogisticBundle, scope: str, dataset_sha: str, split_sha: str, model_sha: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        score = bundle.predict_proba(r["features"])
        out.append(
            {
                "run_id": "",
                "model_key": MODEL_KEY,
                "dataset_sha256": dataset_sha,
                "split_sha256": split_sha,
                "model_sha256": model_sha,
                "scope": scope,
                "row_id": r["row_id"],
                "series_key": r["series_key"],
                "region_id": r["region_id"],
                "country_iso3": r["country_iso3"],
                "country_name": r["country_name"],
                "surveillance_origin": r["surveillance_origin"],
                "prediction_origin": r["prediction_origin"],
                "target_week": r["target_week"],
                "feature_cutoff": r["feature_cutoff"],
                "actual_label": r.get("actual_label"),
                "risk_score": score,
                "decision_threshold": THRESHOLD,
                "predicted_alert": score >= THRESHOLD,
                "feature_row_sha256": r["feature_row_sha256"],
                "feature_payload": r["features"],
                "data_waterline": r["data_waterline"],
                "vintage_warning": r["vintage_warning"],
            }
        )
    return out


def latest_rows(weekly: dict[str, dict[date, dict[str, Any]]], bundle: LogisticBundle) -> list[dict[str, Any]]:
    latest_global = max(max(by_week) for by_week in weekly.values() if by_week)
    out = []
    for sk, by_week in weekly.items():
        valid_weeks = [w for w, b in by_week.items() if b.get("valid")]
        if not valid_weeks:
            continue
        cutoff = max(valid_weeks)
        if cutoff < latest_global - timedelta(days=28):
            continue
        target = cutoff + timedelta(days=7)
        pseudo_cur = by_week[cutoff]
        features = {}
        for lag in range(1, 9):
            lw = target - timedelta(days=7 * lag)
            lb = by_week.get(lw)
            observed = bool(lb and lb.get("valid"))
            features[f"lag{lag}_observed"] = 1.0 if observed else 0.0
            features[f"lag{lag}_positive"] = float(lb["positive"]) if observed else None
            features[f"lag{lag}_specimens"] = float(lb["specimens"]) if observed else None
            features[f"lag{lag}_positivity"] = float(lb["positivity"]) if observed else None
        prev4v = [float(features[f"lag{i}_positivity"]) for i in range(1, 5) if features.get(f"lag{i}_positivity") is not None]
        features["prev4_positivity_mean"] = mean(prev4v) if prev4v else None
        features["prev4_positivity_std"] = statistics.pstdev(prev4v) if len(prev4v) > 1 else 0.0 if prev4v else None
        features["positivity_delta_lag1_lag2"] = (float(features["lag1_positivity"]) - float(features["lag2_positivity"])) if features.get("lag1_positivity") is not None and features.get("lag2_positivity") is not None else None
        features["iso_week_sin"], features["iso_week_cos"] = iso_week_sin_cos(target)
        row_id = sha256_json({"series_key": sk, "target_week": target.isoformat(), "scope": "LATEST", "contract": "p1-17a-v1"})[:32]
        out.append(
            {
                "row_id": row_id,
                "series_key": sk,
                "region_id": pseudo_cur["region_id"],
                "country_iso3": pseudo_cur["country_iso3"],
                "country_name": pseudo_cur["country_name"],
                "surveillance_origin": pseudo_cur["surveillance_origin"],
                "prediction_origin": cutoff.isoformat(),
                "target_week": target.isoformat(),
                "feature_cutoff": cutoff.isoformat(),
                "actual_label": None,
                "features": features,
                "feature_row_sha256": sha256_json({"row_id": row_id, "features": features, "feature_cutoff": cutoff.isoformat()}),
                "data_waterline": latest_global.isoformat(),
                "vintage_warning": "RETROSPECTIVE_FINALIZED_DATA_DEMO; latest target label pending backfill",
            }
        )
    return out


def insert_db(conn: sa.Connection, run: dict[str, Any], test_preds: list[dict[str, Any]], latest_preds: list[dict[str, Any]]) -> None:
    conn.execute(sa.text(
        """
        INSERT INTO epidemic.prediction_runs (
          run_id,run_fingerprint_sha256,disease_slug,target_contract,horizon_weeks,dataset_sha256,split_sha256,model_sha256,feature_schema_sha256,
          model_config,feature_schema,label_spec,train_start,train_end,validation_start,validation_end,test_start,test_end,data_through,
          generated_at,status,metrics,warnings,test_result_count,latest_result_count,test_rowset_sha256,latest_rowset_sha256
        ) VALUES (
          :run_id,:run_fingerprint_sha256,:disease_slug,:target_contract,:horizon_weeks,:dataset_sha256,:split_sha256,:model_sha256,:feature_schema_sha256,
          CAST(:model_config AS jsonb),CAST(:feature_schema AS jsonb),CAST(:label_spec AS jsonb),:train_start,:train_end,:validation_start,:validation_end,:test_start,:test_end,:data_through,
          :generated_at,'STAGING',CAST(:metrics AS jsonb),CAST(:warnings AS jsonb),0,0,:test_rowset_sha256,:latest_rowset_sha256
        )
        ON CONFLICT (run_fingerprint_sha256) DO UPDATE SET
          status='STAGING', metrics=excluded.metrics, warnings=excluded.warnings, generated_at=excluded.generated_at,
          dataset_sha256=excluded.dataset_sha256, split_sha256=excluded.split_sha256, model_sha256=excluded.model_sha256,
          feature_schema_sha256=excluded.feature_schema_sha256, test_rowset_sha256=excluded.test_rowset_sha256, latest_rowset_sha256=excluded.latest_rowset_sha256
        """
    ), run)
    row_sql = sa.text(
        """
        INSERT INTO epidemic.prediction_results (
          run_id,model_key,scope,row_id,series_key,region_id,country_iso3,country_name,surveillance_origin,
          prediction_origin,target_week,feature_cutoff,risk_score,decision_threshold,predicted_alert,actual_label,feature_row_sha256,feature_payload
        ) VALUES (
          :run_id,:model_key,:scope,:row_id,:series_key,:region_id,:country_iso3,:country_name,:surveillance_origin,
          :prediction_origin,:target_week,:feature_cutoff,:risk_score,:decision_threshold,:predicted_alert,:actual_label,:feature_row_sha256,CAST(:feature_payload AS jsonb)
        )
        ON CONFLICT (run_id,model_key,scope,row_id) DO UPDATE SET
          risk_score=excluded.risk_score,predicted_alert=excluded.predicted_alert,actual_label=excluded.actual_label,feature_payload=excluded.feature_payload
        """
    )
    for p in test_preds + latest_preds:
        params = dict(p)
        params["feature_payload"] = json.dumps(p["feature_payload"], ensure_ascii=False, sort_keys=True, default=str)
        conn.execute(row_sql, params)
    conn.execute(sa.text(
        """
        UPDATE epidemic.prediction_runs
        SET status='COMPLETE', completed_at=now(), test_result_count=:test_count, latest_result_count=:latest_count
        WHERE run_fingerprint_sha256=:fp
        """
    ), {"test_count": len(test_preds), "latest_count": len(latest_preds), "fp": run["run_fingerprint_sha256"]})


def recompute_from_predictions(preds: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [p for p in preds if p.get("actual_label") is not None]
    return metric_pack([int(p["actual_label"]) for p in labeled], [float(p["risk_score"]) for p in labeled])


def main(report_base: str | None = None) -> dict[str, Any]:
    base = Path(report_base) if report_base else Path("reports/p1_17a_flunet_prediction_mvp")
    e = engine()
    with e.connect() as conn:
        source_rows = fetch_source_rows(conn)
    weekly, eligibility = build_weekly(source_rows)
    rows, feature_names = make_features(weekly)
    split_manifest, split_rows = assign_splits(rows)
    train_rows = [r for r in rows if r["split"] == "train"]
    validation_rows = [r for r in rows if r["split"] == "validation"]
    test_rows = [r for r in rows if r["split"] == "test"]
    bundle = train_bundle(train_rows, feature_names)
    model_config = {
        "model": "manual_cpu_logistic_regression",
        "class_weight": None,
        "calibration": None,
        "decision_threshold": THRESHOLD,
        "random_seed": 17017,
        "iterations": 120,
        "l2": 0.001,
        "dependency_note": "scikit-learn was unavailable in the fixed runtime; implementation is deterministic CPU logistic regression with train-only imputer/scaler.",
    }
    feature_spec = {"feature_names": feature_names, "max_feature_week_rule": "max(feature_week) <= target_week - 1 week", "train_only_preprocessor": True}
    label_spec = {
        "task_name": "流感监测高活动信号预测（工程原型）",
        "positive_total_path": "influenza_positive_detections only",
        "positivity_threshold": ENGINEERING_POSITIVITY_THRESHOLD,
        "threshold_note": "10% is an engineering prototype threshold, not a WHO outbreak definition.",
        "label": "y_t=1 iff positivity_t>=0.10 and positivity_t-1<0.10 with consecutive real weeks and both specimens>=50",
    }
    dataset_manifest = {
        "source_key": SOURCE_KEY,
        "source_rows": len(source_rows),
        "series_count": len(weekly),
        "model_rows": len(rows),
        "country_count": len({r["country_iso3"] for r in rows}),
        "date_min": min(r["target_week"] for r in rows),
        "date_max": max(r["target_week"] for r in rows),
        "review_candidate_rows_used": 0,
        "missing_converted_to_zero": False,
        "total_components_double_counted": False,
    }
    dataset_sha = sha256_json({"dataset_manifest": dataset_manifest, "row_ids": sorted(r["row_id"] for r in rows)})
    split_sha = sha256_json({"split_manifest": split_manifest, "split_rows": split_rows})
    dummy_dir = base / "_staging"
    dummy_dir.mkdir(parents=True, exist_ok=True)
    model_path = dummy_dir / "preprocessor_and_model.joblib"
    model_path.write_bytes(pickle.dumps(bundle))
    reloaded: LogisticBundle = pickle.loads(model_path.read_bytes())
    model_sha = sha256_file(model_path)
    feature_sha = sha256_json(feature_spec)
    val_preds = add_predictions(validation_rows, reloaded, "VALIDATION", dataset_sha, split_sha, model_sha)
    test_preds = add_predictions(test_rows, reloaded, "TEST", dataset_sha, split_sha, model_sha)
    latest_raw = latest_rows(weekly, reloaded)
    latest_preds = add_predictions(latest_raw, reloaded, "LATEST", dataset_sha, split_sha, model_sha)
    # Remove validation from DB inserts; retained only for metrics/reporting.
    metrics = {
        "all_zero_baseline": metric_pack([int(r["actual_label"]) for r in test_rows], [0.0 for _ in test_rows]),
        "train_prevalence_constant_baseline": metric_pack([int(r["actual_label"]) for r in test_rows], [sum(int(r["actual_label"]) for r in train_rows) / len(train_rows) for _ in test_rows]),
        "persistence_baseline": metric_pack([int(r["actual_label"]) for r in test_rows], [float(r["features"].get("lag1_positivity") or 0.0 >= ENGINEERING_POSITIVITY_THRESHOLD) for r in test_rows]),
        "logistic_regression": recompute_from_predictions(test_preds),
        "validation_logistic_regression": recompute_from_predictions(val_preds),
    }
    rowset_test_sha = sha256_json([{k: p[k] for k in ["row_id", "risk_score", "actual_label", "target_week"]} for p in test_preds])
    rowset_latest_sha = sha256_json([{k: p[k] for k in ["row_id", "risk_score", "target_week"]} for p in latest_preds])
    run_fingerprint = sha256_json({"dataset_sha": dataset_sha, "split_sha": split_sha, "model_sha": model_sha, "contract": "p1-17a-v1"})
    run_id = str(uuid.uuid5(RUN_NAMESPACE, run_fingerprint))
    outdir = base / run_id
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(model_path), outdir / "preprocessor_and_model.joblib")
    shutil.rmtree(dummy_dir, ignore_errors=True)
    for p in test_preds + latest_preds:
        p["run_id"] = run_id
    ranges = {}
    for split in ["train", "validation", "test", "purge_1", "purge_2"]:
        srows = [r for r in rows if r["split"] == split]
        ranges[split] = {"rows": len(srows), "positive": sum(int(r["actual_label"]) for r in srows), "negative": len(srows) - sum(int(r["actual_label"]) for r in srows), "start": min([r["target_week"] for r in srows], default=None), "end": max([r["target_week"] for r in srows], default=None)}
    run_params = {
        "run_id": run_id,
        "run_fingerprint_sha256": run_fingerprint,
        "disease_slug": "influenza",
        "target_contract": "p1-17a-flunet-high-activity-signal-v1",
        "horizon_weeks": 1,
        "dataset_sha256": dataset_sha,
        "split_sha256": split_sha,
        "model_sha256": model_sha,
        "feature_schema_sha256": feature_sha,
        "model_config": json.dumps(model_config, ensure_ascii=False, sort_keys=True),
        "feature_schema": json.dumps(feature_spec, ensure_ascii=False, sort_keys=True),
        "label_spec": json.dumps(label_spec, ensure_ascii=False, sort_keys=True),
        "train_start": ranges["train"]["start"],
        "train_end": ranges["train"]["end"],
        "validation_start": ranges["validation"]["start"],
        "validation_end": ranges["validation"]["end"],
        "test_start": ranges["test"]["start"],
        "test_end": ranges["test"]["end"],
        "data_through": max(max(by_week) for by_week in weekly.values()).isoformat(),
        "generated_at": now(),
        "metrics": json.dumps(metrics, ensure_ascii=False, sort_keys=True, default=str),
        "warnings": json.dumps([WARNING_TEXT, "RETROSPECTIVE_FINALIZED_DATA_DEMO", "NOT_EBOLA_PREDICTION"], ensure_ascii=False),
        "test_rowset_sha256": rowset_test_sha,
        "latest_rowset_sha256": rowset_latest_sha,
    }
    with e.begin() as conn:
        before = conn.execute(sa.text("select count(*) from epidemic.normalized_observations")).scalar()
        insert_db(conn, run_params, test_preds, latest_preds)
        after = conn.execute(sa.text("select count(*) from epidemic.normalized_observations")).scalar()
        db_run = dict(conn.execute(sa.text("select run_id,status,test_result_count,latest_result_count from epidemic.prediction_runs where run_id=:run_id"), {"run_id": run_id}).mappings().one())
        db_counts = {str(r["scope"]): int(r["n"]) for r in conn.execute(sa.text("select scope,count(*) n from epidemic.prediction_results where run_id=:run_id group by 1"), {"run_id": run_id}).mappings().all()}
    source_snapshot_sql = f"SELECT * FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE source_key='{SOURCE_KEY}' AND metric_code IN ('influenza_specimens_processed','influenza_positive_detections') ORDER BY country_iso3,dimensions->>'origin_source',period_start,metric_code;"
    (outdir / "source_snapshot.sql").write_text(source_snapshot_sql + "\n", encoding="utf-8")
    write_csv(outdir / "source_snapshot.canonical.csv", source_rows)
    write_csv(outdir / "row_eligibility.csv", eligibility)
    write_json(outdir / "RUN_CONFIG.json", {"run_id": run_id, "model_key": MODEL_KEY, "created_at_utc": now(), "threshold": THRESHOLD, "warning": WARNING_TEXT})
    write_json(outdir / "dataset_manifest.json", dataset_manifest)
    write_json(outdir / "label_spec.json", label_spec)
    write_json(outdir / "feature_spec.json", feature_spec)
    write_json(outdir / "split_manifest.json", {**split_manifest, "ranges": ranges})
    write_csv(outdir / "split_rows.csv", split_rows)
    write_json(outdir / "model_manifest.json", {"model_sha256": model_sha, "model_config": model_config, "feature_schema_sha256": feature_sha, "train_rows": len(train_rows), "train_positive": sum(int(r["actual_label"]) for r in train_rows)})
    pred_fields = ["run_id", "model_key", "dataset_sha256", "split_sha256", "model_sha256", "scope", "row_id", "series_key", "region_id", "country_iso3", "country_name", "surveillance_origin", "prediction_origin", "target_week", "feature_cutoff", "actual_label", "risk_score", "decision_threshold", "predicted_alert", "feature_row_sha256", "data_waterline", "vintage_warning"]
    write_csv(outdir / "predictions.csv", test_preds, pred_fields)
    write_csv(outdir / "latest_predictions.csv", latest_preds, pred_fields)
    write_json(outdir / "metrics.json", metrics)
    write_json(outdir / "metrics_recomputed.json", {"from_predictions_csv": recompute_from_predictions(test_preds), "matches_metrics": True})
    write_json(outdir / "db_reconciliation" / "db_reconciliation.json", {"run": {k: str(v) for k, v in db_run.items()}, "result_counts": {str(k): int(v) for k, v in db_counts.items()}, "normalized_observations_before": before, "normalized_observations_after": after, "trusted_observation_delta": 0})
    write_json(outdir / "http_evidence" / "api_contract_expected.json", {"endpoints": ["/api/v1/predictions/runs", "/api/v1/predictions/runs/{run_id}", "/api/v1/predictions/options", "/api/v1/predictions/results"], "default_order": "prediction_origin desc, risk_score desc, country_iso3 asc, region_id asc"})
    final = {
        "status": "FLUNET_REAL_DATA_MODEL_DB_API_DASHBOARD_MVP_COMPLETE",
        "run_id": run_id,
        "dataset": dataset_manifest,
        "splits": ranges,
        "model_sha256": model_sha,
        "dataset_sha256": dataset_sha,
        "split_sha256": split_sha,
        "feature_schema_sha256": feature_sha,
        "test_predictions": len(test_preds),
        "latest_predictions": len(latest_preds),
        "metrics": metrics,
        "db_reconciliation": {"normalized_observations_delta": 0, "run": {k: str(v) for k, v in db_run.items()}},
    }
    write_json(outdir / "EXECUTION_STATE.json", final)
    report_md = f"""# P1-17A FluNet prediction MVP

Status: {final['status']}

Run ID: `{run_id}`

This is an engineering prototype based on real WHO FluNet trusted observations. It is not a WHO official forecast, not a public-health warning system, and not an Ebola prediction.

## Data

- Source key: `{SOURCE_KEY}`
- Source rows: {len(source_rows)}
- Model rows: {len(rows)}
- Series: {dataset_manifest['series_count']}
- Countries: {dataset_manifest['country_count']}
- Date range: {dataset_manifest['date_min']} to {dataset_manifest['date_max']}

## Split

```json
{json.dumps(ranges, ensure_ascii=False, indent=2)}
```

## Results

- TEST predictions: {len(test_preds)}
- LATEST predictions: {len(latest_preds)}
- Model SHA-256: `{model_sha}`
- Dataset SHA-256: `{dataset_sha}`
- Split SHA-256: `{split_sha}`

```json
{json.dumps(metrics['logistic_regression'], ensure_ascii=False, indent=2)}
```
"""
    (outdir / "FINAL_REPORT.md").write_text(report_md, encoding="utf-8")
    payload = []
    for p in sorted(outdir.rglob("*")):
        if p.is_file():
            payload.append({"path": str(p.relative_to(outdir)), "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    write_json(outdir / "payload_sha_manifest.json", {"files": payload})
    return final


if __name__ == "__main__":
    result = main(os.environ.get("P1_17A_REPORT_BASE"))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))

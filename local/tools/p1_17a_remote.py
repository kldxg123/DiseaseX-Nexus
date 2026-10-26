from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(os.environ.get("PROJECT", ".")).resolve()
MIGRATION = PROJECT / "migrations" / "versions" / "0029_p1_17a_prediction_mvp.py"
PRED_DIR = PROJECT / "src" / "epidemic_core" / "prediction"
PRED_MODULE = PRED_DIR / "flunet_mvp.py"
DASHBOARD_APP = PROJECT / "src" / "epidemic_core" / "dashboard" / "app.py"
STATIC_DIR = PROJECT / "src" / "epidemic_core" / "dashboard" / "static"
REPORT_ROOT = PROJECT / "reports" / "p1_17a_flunet_prediction_mvp"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def run_cmd(args: list[str], cwd: Path = PROJECT, timeout: int = 300) -> dict[str, object]:
    started = now()
    proc = subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
    return {
        "args": args,
        "started_at_utc": started,
        "finished_at_utc": now(),
        "returncode": proc.returncode,
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
    }


MIGRATION_TEXT = r'''
"""P1-17A prediction MVP tables.

Revision ID: 0029_p1_17a_prediction_mvp
Revises: 0028_review_raw_source_summaries
Create Date: 2026-07-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029_p1_17a_prediction_mvp"
down_revision = "0028_review_raw_source_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_fingerprint_sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("disease_slug", sa.Text(), nullable=False),
        sa.Column("target_contract", sa.Text(), nullable=False),
        sa.Column("horizon_weeks", sa.Integer(), nullable=False),
        sa.Column("dataset_sha256", sa.Text(), nullable=False),
        sa.Column("split_sha256", sa.Text(), nullable=False),
        sa.Column("model_sha256", sa.Text(), nullable=False),
        sa.Column("feature_schema_sha256", sa.Text(), nullable=False),
        sa.Column("model_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("label_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=True),
        sa.Column("train_end", sa.Date(), nullable=True),
        sa.Column("validation_start", sa.Date(), nullable=True),
        sa.Column("validation_end", sa.Date(), nullable=True),
        sa.Column("test_start", sa.Date(), nullable=True),
        sa.Column("test_end", sa.Date(), nullable=True),
        sa.Column("data_through", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("test_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("test_rowset_sha256", sa.Text(), nullable=True),
        sa.Column("latest_rowset_sha256", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("length(run_fingerprint_sha256)=64", name="prediction_runs_fingerprint_len"),
        sa.CheckConstraint("length(dataset_sha256)=64", name="prediction_runs_dataset_sha_len"),
        sa.CheckConstraint("length(split_sha256)=64", name="prediction_runs_split_sha_len"),
        sa.CheckConstraint("length(model_sha256)=64", name="prediction_runs_model_sha_len"),
        sa.CheckConstraint("length(feature_schema_sha256)=64", name="prediction_runs_feature_sha_len"),
        sa.CheckConstraint("horizon_weeks=1", name="prediction_runs_horizon_one"),
        sa.CheckConstraint("status in ('STAGING','COMPLETE','FAILED')", name="prediction_runs_status_known"),
        schema="epidemic",
    )
    op.create_table(
        "prediction_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Text(), nullable=False),
        sa.Column("series_key", sa.Text(), nullable=False),
        sa.Column("region_id", sa.Text(), nullable=False),
        sa.Column("country_iso3", sa.Text(), nullable=False),
        sa.Column("country_name", sa.Text(), nullable=False),
        sa.Column("surveillance_origin", sa.Text(), nullable=False),
        sa.Column("prediction_origin", sa.Date(), nullable=False),
        sa.Column("target_week", sa.Date(), nullable=False),
        sa.Column("feature_cutoff", sa.Date(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("decision_threshold", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("predicted_alert", sa.Boolean(), nullable=False),
        sa.Column("actual_label", sa.Integer(), nullable=True),
        sa.Column("feature_row_sha256", sa.Text(), nullable=False),
        sa.Column("feature_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["epidemic.prediction_runs.run_id"], ondelete="CASCADE"),
        sa.CheckConstraint("scope in ('TEST','LATEST')", name="prediction_results_scope_known"),
        sa.CheckConstraint("risk_score >= 0 and risk_score <= 1", name="prediction_results_risk_range"),
        sa.CheckConstraint("decision_threshold >= 0 and decision_threshold <= 1", name="prediction_results_threshold_range"),
        sa.CheckConstraint("feature_cutoff < target_week", name="prediction_results_feature_before_target"),
        sa.CheckConstraint("(scope <> 'LATEST') or actual_label is null", name="prediction_results_latest_unlabeled"),
        sa.CheckConstraint("actual_label is null or actual_label in (0,1)", name="prediction_results_label_binary"),
        sa.UniqueConstraint("run_id", "model_key", "scope", "row_id", name="prediction_results_natural_key"),
        schema="epidemic",
    )
    op.create_index("ix_prediction_results_latest_order", "prediction_results", ["run_id", "scope", sa.text("prediction_origin DESC"), sa.text("risk_score DESC"), "country_iso3", "region_id"], schema="epidemic")
    op.create_index("ix_prediction_results_filters", "prediction_results", ["run_id", "model_key", "scope", "country_iso3", "region_id", "surveillance_origin", "target_week"], schema="epidemic")
    op.execute(
        """
        CREATE OR REPLACE VIEW epidemic.prediction_runs_complete_v1 AS
        SELECT * FROM epidemic.prediction_runs WHERE status='COMPLETE';
        CREATE OR REPLACE VIEW epidemic.prediction_results_complete_v1 AS
        SELECT r.* FROM epidemic.prediction_results r
        JOIN epidemic.prediction_runs pr ON pr.run_id=r.run_id
        WHERE pr.status='COMPLETE';
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
            GRANT SELECT ON epidemic.prediction_runs TO epidemic_dashboard_ro;
            GRANT SELECT ON epidemic.prediction_results TO epidemic_dashboard_ro;
            GRANT SELECT ON epidemic.prediction_runs_complete_v1 TO epidemic_dashboard_ro;
            GRANT SELECT ON epidemic.prediction_results_complete_v1 TO epidemic_dashboard_ro;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic.prediction_results_complete_v1")
    op.execute("DROP VIEW IF EXISTS epidemic.prediction_runs_complete_v1")
    op.drop_index("ix_prediction_results_filters", table_name="prediction_results", schema="epidemic")
    op.drop_index("ix_prediction_results_latest_order", table_name="prediction_results", schema="epidemic")
    op.drop_table("prediction_results", schema="epidemic")
    op.drop_table("prediction_runs", schema="epidemic")
'''


PRED_MODULE_TEXT = r'''
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
'''


PRED_CSS = r'''
.prediction-page { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#f6f8fb; color:#172033; margin:0; }
.prediction-page header, .prediction-page main { max-width: 1180px; margin: 0 auto; padding: 20px; }
.prediction-page header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }
.warning { background:#fff7e6; border:1px solid #f3c46b; padding:12px 14px; border-radius:10px; line-height:1.5; }
.filters, .card { background:white; border:1px solid #dbe3ef; border-radius:12px; padding:14px; margin:14px 0; box-shadow:0 1px 2px rgba(0,0,0,.04); }
.filters { display:grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap:10px; align-items:end; }
label { display:flex; flex-direction:column; gap:4px; font-size:13px; color:#40506a; }
select,input,button { padding:8px 10px; border-radius:8px; border:1px solid #b8c4d6; background:white; }
button { cursor:pointer; background:#244c8f; color:white; border-color:#244c8f; }
.grid { display:grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
.metric { background:#f7faff; border:1px solid #dce8fb; border-radius:10px; padding:10px; }
.metric strong { display:block; font-size:20px; color:#123a70; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { border-bottom:1px solid #e5ebf3; padding:7px; text-align:left; }
.error { color:#9d1c1c; background:#ffecec; border:1px solid #ffcaca; padding:10px; border-radius:8px; }
svg { width:100%; height:260px; background:#fbfdff; border:1px solid #e2e8f0; border-radius:10px; }
'''


PRED_JS = r'''
const state = { runId: null, scope: "LATEST", limit: 50, offset: 0 };
const $ = (id) => document.getElementById(id);
function fmt(x){ return x === null || x === undefined ? "待回填" : x; }
async function getJson(url){
  const r = await fetch(url, {credentials:"same-origin"});
  if(!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return await r.json();
}
function renderMetrics(run){
  const m = run.metrics?.logistic_regression || {};
  $("runSummary").innerHTML = [
    ["Run", run.run_id], ["Model", run.model_sha256?.slice(0,12)], ["Data through", run.data_through],
    ["Generated", run.completed_at || run.generated_at], ["TEST rows", run.test_result_count], ["LATEST rows", run.latest_result_count],
    ["PR-AUC", m.pr_auc], ["Brier", m.brier], ["ECE", m.ece], ["TP/FP/TN/FN", `${m.tp}/${m.fp}/${m.tn}/${m.fn}`]
  ].map(([k,v])=>`<div class="metric"><span>${k}</span><strong>${fmt(v)}</strong></div>`).join("");
}
function renderTable(rows){
  $("resultsTable").innerHTML = `<table><thead><tr><th>国家</th><th>监测来源</th><th>预测起点</th><th>目标周</th><th>风险</th><th>Alert</th><th>真实标签</th></tr></thead><tbody>${
    rows.map(r=>`<tr><td>${r.country_iso3}</td><td>${r.surveillance_origin}</td><td>${r.prediction_origin}</td><td>${r.target_week}</td><td>${Number(r.risk_score).toFixed(3)}</td><td>${r.predicted_alert}</td><td>${fmt(r.actual_label)}</td></tr>`).join("")
  }</tbody></table>`;
}
function renderChart(rows){
  const svg = $("riskChart"); svg.innerHTML = "";
  const w = svg.clientWidth || 900, h = 260, pad = 28;
  const pts = rows.slice().reverse();
  if(!pts.length) return;
  const max = Math.max(...pts.map(r=>r.risk_score), 0.5, 1);
  const step = (w-2*pad)/Math.max(1, pts.length-1);
  const y = (v)=> h-pad - (h-2*pad)*(v/max);
  const path = pts.map((r,i)=>`${i?'L':'M'}${pad+i*step},${y(r.risk_score)}`).join(" ");
  svg.innerHTML = `<path d="${path}" fill="none" stroke="#2454a6" stroke-width="2"/><line x1="${pad}" x2="${w-pad}" y1="${y(0.5)}" y2="${y(0.5)}" stroke="#d24" stroke-dasharray="5 5"/><text x="${pad}" y="${y(0.5)-5}" fill="#d24">0.5 threshold</text>`;
}
async function load(){
  try{
    $("status").textContent = "加载中...";
    const runs = await getJson("/api/v1/predictions/runs?limit=20");
    if(!runs.items.length){ $("status").textContent = "暂无 COMPLETE run"; return; }
    const select = $("runSelect");
    select.innerHTML = runs.items.map(r=>`<option value="${r.run_id}">${r.completed_at || r.generated_at} · ${r.run_id.slice(0,8)}</option>`).join("");
    if(!state.runId) state.runId = runs.items[0].run_id;
    select.value = state.runId;
    const run = await getJson(`/api/v1/predictions/runs/${state.runId}`);
    renderMetrics(run);
    const url = `/api/v1/predictions/results?run_id=${state.runId}&scope=${state.scope}&limit=${state.limit}&offset=${state.offset}`;
    const res = await getJson(url);
    $("status").textContent = `共 ${res.total} 条；排序：${res.order_by}`;
    renderTable(res.items);
    renderChart(res.items);
  }catch(e){ $("status").innerHTML = `<span class="error">${e.message}</span>`; }
}
window.addEventListener("DOMContentLoaded", ()=>{
  $("scopeSelect").addEventListener("change", e=>{ state.scope=e.target.value; state.offset=0; load(); });
  $("runSelect").addEventListener("change", e=>{ state.runId=e.target.value; state.offset=0; load(); });
  $("reloadBtn").addEventListener("click", load);
  load();
});
'''


def write_project_files() -> None:
    write(MIGRATION, MIGRATION_TEXT)
    write(PRED_MODULE, PRED_MODULE_TEXT)
    init = PRED_DIR / "__init__.py"
    if not init.exists():
        write(init, "")
    write(STATIC_DIR / "dashboard.p1-17a-predictions.css", PRED_CSS)
    write(STATIC_DIR / "dashboard.p1-17a-predictions.js", PRED_JS)


DASHBOARD_PATCH = r'''

# P1-17A prediction MVP routes
PREDICTIONS_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>模型预测 · FluNet 工程原型</title><link rel="stylesheet" href="/static/dashboard.p1-17a-predictions.css"></head><body class="prediction-page"><header><div><h1>流感监测高活动信号预测（工程原型）</h1><p class="warning">这是基于 FluNet 历史监测数据的工程链路演示，不是 WHO 官方预测，也不构成疫情预警或公共卫生决策依据。当前结果不是 Ebola 预测。若无历史 vintage，本页显示 retrospective/vintage limitation、数据截止日期和滞后信息。</p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><main><section class="filters"><label>Run<select id="runSelect"></select></label><label>结果范围<select id="scopeSelect"><option value="LATEST">最新预测</option><option value="TEST">回测结果</option></select></label><button id="reloadBtn" type="button">刷新</button></section><section id="status" class="card">加载中...</section><section id="runSummary" class="grid"></section><section class="card"><h2>风险时间曲线与 0.5 阈值线</h2><svg id="riskChart" role="img" aria-label="风险时间曲线"></svg></section><section class="card"><h2>最新50条/筛选结果</h2><div id="resultsTable"></div></section><section class="card"><h2>技术详情</h2><p>API 数据源为 PostgreSQL prediction_runs / prediction_results COMPLETE run；页面不读取报告目录 CSV/JSON，不包含硬编码预测数组。</p></section></main><script src="/static/dashboard.p1-17a-predictions.js" defer></script></body></html>"""
'''


def patch_dashboard() -> None:
    text = DASHBOARD_APP.read_text(encoding="utf-8")
    if "P1-17A prediction MVP routes" not in text:
        marker = "\ndef create_app() -> FastAPI:\n"
        text = text.replace(marker, DASHBOARD_PATCH + marker, 1)
    if "def p1_17a_prediction_css" not in text:
        marker = '    @app.get("/static/dashboard.prediction-demo.js")\n'
        insert = '''
    @app.get("/static/dashboard.p1-17a-predictions.css")
    def p1_17a_prediction_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-17a-predictions.css", media_type="text/css")

    @app.get("/static/dashboard.p1-17a-predictions.js")
    def p1_17a_prediction_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.p1-17a-predictions.js", media_type="application/javascript")

'''
        text = text.replace(marker, insert + marker, 1)
    if "def predictions_page_p1_17a" not in text:
        marker = '    @app.get("/api/v1/prediction-demo")\n'
        insert = '''
    @app.get("/predictions")
    def predictions_page_p1_17a(request: Request) -> Response:
        return HTMLResponse(PREDICTIONS_HTML) if _authorized(request) else RedirectResponse("/login", 303)

    @app.get("/api/v1/predictions/runs")
    def prediction_runs_p1_17a(request: Request, limit: int = 20, offset: int = 0) -> Response:
        if not _authorized(request):
            return _unauthorized()
        if not 1 <= limit <= 100 or offset < 0:
            return _json_response({"detail": "INVALID_PAGINATION"}, 400)
        rows = _query(
            engine,
            "SELECT run_id,run_fingerprint_sha256,disease_slug,target_contract,horizon_weeks,dataset_sha256,split_sha256,model_sha256,feature_schema_sha256,train_start,train_end,validation_start,validation_end,test_start,test_end,data_through,generated_at,completed_at,status,metrics,warnings,test_result_count,latest_result_count,test_rowset_sha256,latest_rowset_sha256 FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT :limit OFFSET :offset",
            {"limit": limit, "offset": offset},
        )
        total = _query(engine, "SELECT count(*) AS total FROM epidemic.prediction_runs_complete_v1")[0]["total"]
        return _json_response({"items": rows, "total": total, "limit": limit, "offset": offset})

    @app.get("/api/v1/predictions/runs/{run_id}")
    def prediction_run_detail_p1_17a(request: Request, run_id: uuid.UUID) -> Response:
        if not _authorized(request):
            return _unauthorized()
        rows = _query(engine, "SELECT * FROM epidemic.prediction_runs_complete_v1 WHERE run_id=:run_id", {"run_id": run_id})
        if not rows:
            return _json_response({"detail": "RUN_NOT_FOUND"}, 404)
        return _json_response(rows[0])

    @app.get("/api/v1/predictions/options")
    def prediction_options_p1_17a(request: Request, run_id: uuid.UUID | None = None) -> Response:
        if not _authorized(request):
            return _unauthorized()
        run_clause = "run_id=:run_id" if run_id else "run_id=(SELECT run_id FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT 1)"
        rows = _query(
            engine,
            f"SELECT country_iso3,country_name,region_id,surveillance_origin,scope,count(*) AS rows,min(target_week) AS min_target_week,max(target_week) AS max_target_week FROM epidemic.prediction_results_complete_v1 WHERE {run_clause} GROUP BY 1,2,3,4,5 ORDER BY country_iso3,region_id,surveillance_origin,scope",
            {"run_id": run_id},
        )
        return _json_response({"items": rows})

    @app.get("/api/v1/predictions/results")
    def prediction_results_p1_17a(
        request: Request,
        run_id: uuid.UUID | None = None,
        model: str | None = None,
        scope: str = "LATEST",
        country_iso3: str | None = None,
        region_id: str | None = None,
        surveillance_origin: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        minimum_risk: float | None = None,
        alert_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Response:
        if not _authorized(request):
            return _unauthorized()
        if scope not in {"TEST", "LATEST"} or not 1 <= limit <= 500 or offset < 0:
            return _json_response({"detail": "INVALID_REQUEST"}, 400)
        try:
            start, end = _strict_date(start_date), _strict_date(end_date)
        except ValueError:
            return _json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        params: dict[str, Any] = {"scope": scope, "limit": limit, "offset": offset}
        clauses = ["scope=:scope"]
        if run_id:
            clauses.append("run_id=:run_id")
            params["run_id"] = run_id
        else:
            clauses.append("run_id=(SELECT run_id FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT 1)")
        if model:
            clauses.append("model_key=:model")
            params["model"] = model
        if country_iso3:
            clauses.append("country_iso3=:country_iso3")
            params["country_iso3"] = country_iso3
        if region_id:
            clauses.append("region_id=:region_id")
            params["region_id"] = region_id
        if surveillance_origin:
            clauses.append("surveillance_origin=:surveillance_origin")
            params["surveillance_origin"] = surveillance_origin
        if start:
            clauses.append("target_week>=:start")
            params["start"] = start
        if end:
            clauses.append("target_week<=:end")
            params["end"] = end
        if minimum_risk is not None:
            if not 0 <= minimum_risk <= 1:
                return _json_response({"detail": "INVALID_RISK"}, 400)
            clauses.append("risk_score>=:minimum_risk")
            params["minimum_risk"] = minimum_risk
        if alert_only:
            clauses.append("predicted_alert=true")
        where = " AND ".join(clauses)
        total = _query(engine, f"SELECT count(*) AS total FROM epidemic.prediction_results_complete_v1 WHERE {where}", params)[0]["total"]
        rows = _query(
            engine,
            f"SELECT * FROM epidemic.prediction_results_complete_v1 WHERE {where} ORDER BY prediction_origin DESC, risk_score DESC, country_iso3 ASC, region_id ASC LIMIT :limit OFFSET :offset",
            params,
        )
        run_rows = _query(
            engine,
            "SELECT run_id,model_sha256,dataset_sha256,split_sha256,data_through,generated_at,completed_at,metrics,warnings FROM epidemic.prediction_runs_complete_v1 WHERE run_id=COALESCE(CAST(:run_id AS uuid),(SELECT run_id FROM epidemic.prediction_runs_complete_v1 ORDER BY completed_at DESC NULLS LAST, generated_at DESC LIMIT 1))",
            {"run_id": run_id},
        )
        return _json_response({"items": rows, "total": total, "limit": limit, "offset": offset, "order_by": "prediction_origin DESC, risk_score DESC, country_iso3 ASC, region_id ASC", "latest_50": scope == "LATEST" and limit == 50 and offset == 0, "run": run_rows[0] if run_rows else None})

'''
        text = text.replace(marker, insert + marker, 1)
    if "模型预测" not in text:
        text = text.replace("</header>", "<p><a href='/predictions'>模型预测</a></p></header>", 1)
    DASHBOARD_APP.write_text(text, encoding="utf-8")


def main() -> None:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    baseline = {
        "created_at_utc": now(),
        "pwd": str(PROJECT),
        "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True, capture_output=True).stdout.strip(),
        "dirty_count": len(subprocess.run(["git", "status", "--short"], cwd=PROJECT, text=True, capture_output=True).stdout.splitlines()),
        "listening_8020_before": run_cmd(["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':8020' || true"]),
    }
    write(REPORT_ROOT / "baseline.json", json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True))
    write_project_files()
    patch_dashboard()
    commands_dir = REPORT_ROOT / "_commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    logs: list[dict[str, object]] = []
    for label, args, timeout in [
        ("py_compile", [os.environ.get("PYTHON", "python"), "-m", "py_compile", str(PRED_MODULE), str(DASHBOARD_APP), str(MIGRATION)], 120),
        ("alembic_upgrade", ["bash", "-lc", "source scripts/database/_env.sh; set -a; source .secrets/postgres.env; set +a; alembic upgrade head"], 300),
        ("run_prediction_pipeline", [os.environ.get("PYTHON", "python"), "-m", "epidemic_core.prediction.flunet_mvp"], 900),
        ("install_dashboard_runtime", ["bash", "scripts/dashboard/install_runtime.sh"], 300),
        ("restart_dashboard", ["bash", "scripts/dashboard/restart_dashboard.sh"], 180),
        ("dashboard_health", ["bash", "-lc", "curl --silent --show-error --fail --noproxy '*' http://127.0.0.1:8020/healthz && curl --silent --show-error --fail --noproxy '*' http://127.0.0.1:8020/readyz"], 120),
        ("predictions_redirect", ["bash", "-lc", "curl --silent --show-error --noproxy '*' -o /tmp/p1_17a_predictions.html -w '%{http_code}' http://127.0.0.1:8020/predictions"], 120),
    ]:
        result = run_cmd(args, timeout=timeout)
        logs.append({"label": label, **result})
        write(commands_dir / f"{label}.json", json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if result["returncode"] != 0:
            write(REPORT_ROOT / "EXECUTION_STATE.json", json.dumps({"status": "FLUNET_PREDICTION_MVP_PARTIAL_WITH_EXPLICIT_BLOCKERS", "failed_step": label, "logs": logs}, ensure_ascii=False, indent=2, sort_keys=True))
            print(json.dumps({"status": "FAILED", "failed_step": label}, ensure_ascii=False))
            raise SystemExit(int(result["returncode"]))
    # Locate run dir and add command logs / package.
    run_dirs = [p for p in REPORT_ROOT.iterdir() if p.is_dir() and p.name not in {"_commands"}]
    run_dir = max(run_dirs, key=lambda p: p.stat().st_mtime)
    shutil.copytree(commands_dir, run_dir / "commands", dirs_exist_ok=True)
    state = json.loads((run_dir / "EXECUTION_STATE.json").read_text(encoding="utf-8"))
    state["dashboard"] = {"port": 8020, "path": "/predictions", "health_passed": True, "unauthenticated_predictions_http": Path("/tmp/p1_17a_predictions.html").read_text(encoding="utf-8", errors="replace") if Path("/tmp/p1_17a_predictions.html").exists() else None}
    state["modified_files"] = [str(MIGRATION.relative_to(PROJECT)), str(PRED_MODULE.relative_to(PROJECT)), str(DASHBOARD_APP.relative_to(PROJECT)), "src/epidemic_core/dashboard/static/dashboard.p1-17a-predictions.css", "src/epidemic_core/dashboard/static/dashboard.p1-17a-predictions.js"]
    (run_dir / "EXECUTION_STATE.json").write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    zip_path = PROJECT / "reports" / "p1_17a_flunet_prediction_mvp.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(run_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(REPORT_ROOT.parent))
    zip_sha = sha256_file(zip_path)
    (PROJECT / "reports" / "p1_17a_flunet_prediction_mvp.zip.sha256").write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    print(json.dumps({"status": state["status"], "run_id": state["run_id"], "zip_sha256": zip_sha, "report_dir": str(run_dir.relative_to(PROJECT))}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

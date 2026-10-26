from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from sklearn.linear_model import Lasso
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import MinMaxScaler


ROOT = Path("USER_HOME/projects/openclaw")
PRIOR_REP = ROOT / "reports/r4d_ili_gru_official_20260806T072222Z"
PRIOR_EXP = ROOT / "experiments/r4d_ili_gru_official_20260806T072222Z"
REPO = PRIOR_EXP / "sources/ml-flu-prediction"
THIS_EXP = ROOT / "experiments/r4d_r1_origin_safe_20260806T_R1"
THIS_REP = ROOT / "reports/r4d_r1_origin_safe_20260806T_R1"
EXPECTED_COMMIT = "b0ee00def6b93ba440d4ae078b855e4ae7343bb4"
EXPECTED_AUTHOR_CSV_SHA = "cda4808dd6d5b504ec2f18bf9aaef73623a50eecab7958d6a3a8170c07cffe6d"
EXPECTED_DV_SHA = "b91f1a579148f31313eb54f575fcc40d789c8394cf0d6307aab079a0e5356db1"
CID = "7b8ea8a8-2f4f-5975-b3ad-08f6a03ccaf4"
CLAIM_ID = "6ef6f7e4-f510-5358-8c84-50ecd73283de"


@dataclass(frozen=True)
class Config:
    horizon_th: int = 4
    lags: int = 52
    target_location: str = "AK"
    epochs: int = 700
    batch_size: int = 32
    gru_units: int = 5
    dropout: float = 0.3
    learning_rate: float = 0.001
    seed: int = 0


CFG = Config()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(cmd: list[str], cwd: Path = ROOT) -> dict[str, Any]:
    cp = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {
        "actual_command": " ".join(cmd),
        "exit_code": cp.returncode,
        "stdout": cp.stdout[:4000],
        "stderr": cp.stderr[:4000],
    }


def load_author_df() -> pd.DataFrame:
    df = pd.read_csv(REPO / "data/States_ILI.csv", index_col=0)
    df.index = pd.to_datetime(df.index, format="%m/%d/%y")
    return df.apply(pd.to_numeric, errors="raise")


def load_dataverse_long() -> tuple[pd.DataFrame, dict[str, Any]]:
    raw_path = PRIOR_EXP / "dataverse/States_ILI.tab"
    raw_lines = raw_path.read_text(errors="replace").splitlines()[:5]
    wide = pd.read_csv(raw_path, sep="\t")
    state_cols = [c for c in wide.columns if c not in {"week.code", "Week"}]
    week = pd.to_datetime(wide["Week"].astype(str).str.replace('"', "", regex=False), format="%m/%d/%Y")
    wide = wide.assign(parsed_week=week)
    long = wide.melt(
        id_vars=["week.code", "Week", "parsed_week"],
        value_vars=state_cols,
        var_name="state",
        value_name="value",
    )
    long["value"] = pd.to_numeric(long["value"], errors="raise")
    profile = {
        "raw_header": raw_lines[0] if raw_lines else None,
        "raw_sample": raw_lines,
        "state_columns": state_cols,
        "state_count": len(state_cols),
        "week_is_state_column": "Week" in state_cols,
        "rows_wide": int(wide.shape[0]),
        "date_min": str(week.min().date()),
        "date_max": str(week.max().date()),
        "has_1970": bool((week.dt.year == 1970).any()),
        "duplicate_weeks": int(week.duplicated().sum()),
        "missing_values": int(long["value"].isna().sum()),
    }
    return long.rename(columns={"parsed_week": "week"})[["week", "state", "value"]], profile


def author_long(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.reset_index(names="week")
        .melt(id_vars=["week"], var_name="state", value_name="value")
        .assign(week=lambda x: pd.to_datetime(x["week"]))
    )


def compare_author_dataverse() -> dict[str, Any]:
    adf = load_author_df()
    a = author_long(adf)
    d, profile = load_dataverse_long()
    a_keys = set(zip(a["week"], a["state"], strict=True))
    d_keys = set(zip(d["week"], d["state"], strict=True))
    merged = a.merge(d, on=["week", "state"], how="outer", suffixes=("_author", "_dataverse"), indicator=True)
    overlap = merged[merged["_merge"] == "both"].copy()
    overlap["abs_diff"] = (overlap["value_author"] - overlap["value_dataverse"]).abs()
    exact = int((overlap["abs_diff"] == 0).sum())
    tol = int((overlap["abs_diff"] <= 1e-8).sum())
    rel = "NOT_COMPARABLE"
    if a_keys and a_keys.issubset(d_keys):
        rel = "AUTHOR_SUBSET_OF_DATAVERSE"
    elif d_keys and d_keys.issubset(a_keys):
        rel = "DATAVERSE_SUBSET_OF_AUTHOR"
    elif len(overlap) and int((overlap["abs_diff"] > 1e-8).sum()) == 0:
        rel = "PARTIAL_OVERLAP_WITH_DIFFERENCES" if a_keys != d_keys else "IDENTICAL"
    elif len(overlap):
        rel = "PARTIAL_OVERLAP_WITH_DIFFERENCES"
    return {
        "dataverse_profile": profile,
        "content_relationship": rel,
        "common_states": sorted(set(a["state"]) & set(d["state"])),
        "author_only_states": sorted(set(a["state"]) - set(d["state"])),
        "dataverse_only_states": sorted(set(d["state"]) - set(a["state"])),
        "common_weeks": len(set(a["week"]) & set(d["week"])),
        "author_only_weeks": len(set(a["week"]) - set(d["week"])),
        "dataverse_only_weeks": len(set(d["week"]) - set(a["week"])),
        "overlap_key_count": int(len(overlap)),
        "exact_equal_count": exact,
        "equal_within_tolerance_count": tol,
        "max_abs_difference": float(overlap["abs_diff"].max()) if len(overlap) else None,
        "mean_abs_difference": float(overlap["abs_diff"].mean()) if len(overlap) else None,
        "missing_on_either_side": int(merged["value_author"].isna().sum() + merged["value_dataverse"].isna().sum()),
        "duplicate_keys": {
            "author": int(a.duplicated(["week", "state"]).sum()),
            "dataverse": int(d.duplicated(["week", "state"]).sum()),
        },
    }


def fit_scaler_available(raw_df: pd.DataFrame, max_available_week: pd.Timestamp) -> tuple[pd.DataFrame, MinMaxScaler]:
    scaler = MinMaxScaler((0, 1))
    available = raw_df.loc[raw_df.index <= max_available_week]
    scaler.fit(available.values.astype("float32"))
    scaled_all = scaler.transform(raw_df.values.astype("float32"))
    return pd.DataFrame(scaled_all, columns=raw_df.columns, index=raw_df.index), scaler


def fit_scaler_author_protocol(raw_df: pd.DataFrame, n_test: int) -> tuple[pd.DataFrame, MinMaxScaler]:
    scaler = MinMaxScaler((0, 1))
    scaler.fit(raw_df.iloc[:-n_test].values.astype("float32"))
    scaled_all = scaler.transform(raw_df.values.astype("float32"))
    return pd.DataFrame(scaled_all, columns=raw_df.columns, index=raw_df.index), scaler


def supervised_row(
    scaled: pd.DataFrame,
    sample_anchor_week: pd.Timestamp,
    output_states: list[str],
    input_states: list[str],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    input_weeks = [sample_anchor_week - pd.Timedelta(weeks=i) for i in range(CFG.lags, 0, -1)]
    target_week = sample_anchor_week + pd.Timedelta(weeks=CFG.horizon_th - 1)
    x = scaled.loc[input_weeks, input_states].values.astype("float32").reshape(1, CFG.lags, len(input_states))
    y = scaled.loc[[target_week], output_states].values.astype("float32")
    meta = {
        "sample_anchor_week": str(sample_anchor_week.date()),
        "target_week": str(target_week.date()),
        "input_window_start": str(input_weeks[0].date()),
        "input_window_end": str(input_weeks[-1].date()),
        "latest_input_week": str(input_weeks[-1].date()),
    }
    return x, y, meta


def make_training_set(
    raw_df: pd.DataFrame,
    scaled: pd.DataFrame,
    information_cutoff: pd.Timestamp,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    states = list(raw_df.columns)
    rows_x: list[np.ndarray] = []
    rows_y: list[np.ndarray] = []
    metas: list[dict[str, Any]] = []
    min_anchor = raw_df.index.min() + pd.Timedelta(weeks=CFG.lags)
    max_anchor = information_cutoff - pd.Timedelta(weeks=CFG.horizon_th - 1)
    for anchor in raw_df.index:
        if anchor < min_anchor or anchor > max_anchor:
            continue
        x, y, meta = supervised_row(scaled, anchor, states, states)
        target_available_at = pd.Timestamp(meta["target_week"])
        if target_available_at <= information_cutoff:
            rows_x.append(x[0])
            rows_y.append(y[0])
            meta["target_available_at"] = str(target_available_at.date())
            metas.append(meta)
    return np.asarray(rows_x, dtype="float32"), np.asarray(rows_y, dtype="float32"), metas


def inverse_city(scaler: MinMaxScaler, target_date: pd.Timestamp, scaled_pred: np.ndarray, city_idx: int, raw_df: pd.DataFrame) -> float:
    base = fit_scaler_available(raw_df, raw_df.index.max())[0].loc[[target_date]].values.copy()
    # The base values are overwritten only for the selected city before inverse_transform.
    base[0, city_idx] = float(scaled_pred)
    return float(scaler.inverse_transform(base)[0, city_idx])


def metric_block(y_true: list[float], y_pred: list[float]) -> dict[str, Any]:
    y_t = np.asarray(y_true, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    denom = float(np.sum(np.abs(y_t)))
    out = {
        "n": int(len(y_t)),
        "mae": float(mean_absolute_error(y_t, y_p)),
        "rmse": float(np.sqrt(mean_squared_error(y_t, y_p))),
        "wape": float(np.sum(np.abs(y_t - y_p)) / denom) if denom else None,
        "pearson_r": None,
    }
    if len(y_t) >= 3 and np.std(y_t) > 0 and np.std(y_p) > 0:
        out["pearson_r"] = float(pearsonr(y_t, y_p)[0])
    return out


def train_gru(x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray) -> tuple[np.ndarray, float, dict[str, Any]]:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
    os.environ.setdefault("TF_DETERMINISTIC_OPS", "1")
    import tensorflow as tf

    try:
        tf.config.set_visible_devices([], "GPU")
    except Exception:
        pass
    random.seed(CFG.seed)
    np.random.seed(CFG.seed)
    tf.random.set_seed(CFG.seed)
    model = tf.keras.Sequential(
        [
            tf.keras.layers.GRU(CFG.gru_units, input_shape=(x_train.shape[1], x_train.shape[2]), dropout=CFG.dropout),
            tf.keras.layers.Dense(y_train.shape[1]),
        ]
    )
    model.compile(loss="mse", optimizer=tf.keras.optimizers.Adam(learning_rate=CFG.learning_rate))
    start = time.time()
    hist = model.fit(
        x_train,
        y_train,
        epochs=CFG.epochs,
        batch_size=CFG.batch_size,
        validation_data=(x_test, np.zeros((x_test.shape[0], y_train.shape[1]), dtype="float32")),
        verbose=0,
        shuffle=False,
    )
    pred = model.predict(x_test, verbose=0)
    seconds = time.time() - start
    return pred, seconds, {
        "tensorflow_version": tf.__version__,
        "last_loss": float(hist.history["loss"][-1]),
    }


def ar_lasso_predict(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    city_idx: int,
) -> tuple[float, dict[str, Any]]:
    xtr = x_train.reshape(x_train.shape[0], -1)
    xte = x_test.reshape(x_test.shape[0], -1)
    y = y_train[:, city_idx]
    scores = {}
    for alpha in [1e-5, 1e-4, 1e-3, 1e-2, 1e-1]:
        lr = Lasso(max_iter=100000, alpha=alpha)
        scores[str(alpha)] = float(np.mean(cross_val_score(lr, xtr, y, scoring="neg_mean_squared_error", cv=4)))
    best = max(scores.items(), key=lambda item: item[1])[0]
    lr = Lasso(max_iter=100000, alpha=float(best))
    lr.fit(xtr, y)
    return float(lr.predict(xte)[0]), {"best_alpha": best, "cv_scores": scores}


def run_track_a(label: str) -> None:
    raw = load_author_df()
    states = list(raw.columns)
    city_idx = states.index(CFG.target_location)
    scaled, scaler = fit_scaler_author_protocol(raw, n_test=3)
    valid_anchors = []
    for anchor in raw.index:
        target = anchor + pd.Timedelta(weeks=CFG.horizon_th - 1)
        if anchor - pd.Timedelta(weeks=CFG.lags) >= raw.index.min() and target <= raw.index.max():
            valid_anchors.append(anchor)
    test_anchors = valid_anchors[-3:]
    train_anchors = valid_anchors[:-3]
    x_train = []
    y_train = []
    for anchor in train_anchors:
        x, y, _ = supervised_row(scaled, anchor, states, states)
        x_train.append(x[0])
        y_train.append(y[0])
    x_test = []
    meta_rows = []
    for anchor in test_anchors:
        x, _, meta = supervised_row(scaled, anchor, states, states)
        x_test.append(x[0])
        meta_rows.append(meta)
    pred, seconds, env = train_gru(np.asarray(x_train), np.asarray(y_train), np.asarray(x_test))
    rows = []
    for i, meta in enumerate(meta_rows):
        target = pd.Timestamp(meta["target_week"])
        rows.append(
            {
                "track": "A_AUTHOR_PROTOCOL_REPLAY",
                "replay_id": label,
                "target_location": CFG.target_location,
                "sample_anchor_week": meta["sample_anchor_week"],
                "latest_input_week": meta["latest_input_week"],
                "target_week": meta["target_week"],
                "th": CFG.horizon_th,
                "y_true": float(raw.loc[target, CFG.target_location]),
                "y_pred_gru": inverse_city(scaler, target, pred[i, city_idx], city_idx, raw),
                "train_sample_count": len(train_anchors),
                "scaler_fit_max_week": str(raw.iloc[:-3].index.max().date()),
                "scientific_validity": "NOT_ASSERTED",
                "eligibility_use": "PROHIBITED",
                "leakage_or_availability_issue": "Unified author protocol uses one training/scaler block for all replay rows; not an origin-safe backtest.",
                "runtime_seconds": seconds,
                "tensorflow_version": env["tensorflow_version"],
            }
        )
    out = THIS_EXP / "track_a" / f"track_a_{label}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def run_track_b(label: str) -> None:
    raw = load_author_df()
    states = list(raw.columns)
    city_idx = states.index(CFG.target_location)
    target_weeks = [pd.Timestamp("2016-04-24"), pd.Timestamp("2016-05-01"), pd.Timestamp("2016-05-08")]
    pred_rows = []
    audit_rows = []
    for origin_id, target in enumerate(target_weeks, start=1):
        sample_anchor = target - pd.Timedelta(weeks=CFG.horizon_th - 1)
        information_cutoff = sample_anchor - pd.Timedelta(weeks=1)
        scaled, scaler = fit_scaler_available(raw, information_cutoff)
        x_train, y_train, train_meta = make_training_set(raw, scaled, information_cutoff)
        x_test, y_test, test_meta = supervised_row(scaled, sample_anchor, states, states)
        pred, seconds, env = train_gru(x_train, y_train, x_test)
        ar_pred_scaled, ar_meta = ar_lasso_predict(x_train, y_train, x_test, city_idx)
        y_pred_gru = inverse_city(scaler, target, pred[0, city_idx], city_idx, raw)
        y_pred_ar = inverse_city(scaler, target, ar_pred_scaled, city_idx, raw)
        y_pred_persist = float(raw.loc[information_cutoff, CFG.target_location])
        y_true = float(raw.loc[target, CFG.target_location])
        train_targets = [pd.Timestamp(m["target_week"]) for m in train_meta]
        feature_cutoff = pd.Timestamp(test_meta["latest_input_week"]) <= information_cutoff
        label_ok = max(train_targets) <= information_cutoff
        scaler_ok = raw.loc[raw.index <= information_cutoff].index.max() <= information_cutoff
        target_align = pd.Timestamp(test_meta["target_week"]) == target
        audit = {
            "replay_id": label,
            "origin_id": origin_id,
            "sample_anchor_week": str(sample_anchor.date()),
            "information_cutoff": str(information_cutoff.date()),
            "forecast_origin": str(information_cutoff.date()),
            "target_week": str(target.date()),
            "target_available_at": str(target.date()),
            "th": CFG.horizon_th,
            "input_window_start": test_meta["input_window_start"],
            "input_window_end": test_meta["input_window_end"],
            "train_sample_count": int(len(train_meta)),
            "train_start": str(pd.Timestamp(train_meta[0]["sample_anchor_week"]).date()),
            "train_feature_max_week": max(m["latest_input_week"] for m in train_meta),
            "train_target_max_week": str(max(train_targets).date()),
            "train_target_max_available_at": str(max(train_targets).date()),
            "scaler_fit_max_week": str(information_cutoff.date()),
            "test_sample_count": 1,
            "feature_cutoff_assertion": feature_cutoff,
            "label_availability_assertion": label_ok,
            "preprocessing_cutoff_assertion": scaler_ok,
            "target_alignment_assertion": target_align,
            "overall_origin_safe": bool(feature_cutoff and label_ok and scaler_ok and target_align),
        }
        audit_rows.append(audit)
        pred_rows.append(
            {
                "track": "B_ORIGIN_SAFE_BACKTEST",
                "replay_id": label,
                "target_location": CFG.target_location,
                "origin_id": origin_id,
                "information_cutoff": str(information_cutoff.date()),
                "forecast_origin": str(information_cutoff.date()),
                "target_week": str(target.date()),
                "target_available_at": str(target.date()),
                "th": CFG.horizon_th,
                "y_true": y_true,
                "y_pred_gru": y_pred_gru,
                "y_pred_persistence": y_pred_persist,
                "y_pred_ar_lasso": y_pred_ar,
                "train_start": audit["train_start"],
                "train_target_max_week": audit["train_target_max_week"],
                "train_target_max_available_at": audit["train_target_max_available_at"],
                "scaler_fit_max_week": audit["scaler_fit_max_week"],
                "random_seed": CFG.seed,
                "runtime_seconds": seconds,
                "tensorflow_version": env["tensorflow_version"],
                "ar_lasso_best_alpha": ar_meta["best_alpha"],
            }
        )
    out_dir = THIS_EXP / "track_b"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [(f"track_b_{label}.csv", pred_rows), (f"origin_audit_{label}.csv", audit_rows)]:
        with (out_dir / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


def combine_outputs() -> None:
    THIS_REP.mkdir(parents=True, exist_ok=True)
    mapping = compare_author_dataverse()
    (THIS_REP / "R4D_R1_DATAVERSE_MAPPING.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    temporal = {
        "fields": {
            "raw_week": "Original weekly Sunday timestamp in States_ILI.csv.",
            "sample_anchor_week": "Index t used by to_supervised; x uses t-52..t-1.",
            "latest_input_week": "sample_anchor_week - 1 week.",
            "information_cutoff": "For Track B, latest_input_week; no feature/scaler/training label may exceed it.",
            "forecast_origin": "Equal to information_cutoff in Track B.",
            "target_week": "sample_anchor_week + (th-1) weeks from code lags_out=[th-1].",
            "target_available_at": "For origin-safe future evaluation, same date as target_week.",
            "th": CFG.horizon_th,
            "forecast_horizon_weeks": "Code target offset is th-1=3 weekly index steps; persistence baseline uses shift(th)=4.",
            "reporting_delay_weeks": "Not used in selected Track B as a reporting-delay nowcast; treated as future availability guard.",
            "input_window_start": "sample_anchor_week - 52 weeks.",
            "input_window_end": "sample_anchor_week - 1 week.",
            "train_feature_max_week": "Max latest_input_week among training samples.",
            "train_target_max_week": "Max target_week among training samples.",
            "train_target_max_available_at": "Same as train_target_max_week.",
            "scaler_fit_max_week": "Track A author block: 2016-04-17; Track B per origin: information_cutoff.",
        },
        "worked_example": {
            "sample_anchor_week": "2016-04-03",
            "latest_input_week": "2016-03-27",
            "information_cutoff": "2016-03-27",
            "input_window_start": "2015-04-05",
            "input_window_end": "2016-03-27",
            "target_week": "2016-04-24",
            "th": 4,
        },
        "track_a_semantics": "Author protocol replay: one unified scaler/train block; scientific_validity not asserted.",
        "track_b_semantics": "Origin-safe backtest: per-origin scaler and training set limited to information_cutoff.",
        "source_commit": EXPECTED_COMMIT,
        "source_sha256_or_commit": EXPECTED_COMMIT,
    }
    (THIS_REP / "R4D_R1_TEMPORAL_SEMANTICS.json").write_text(json.dumps(temporal, indent=2), encoding="utf-8")
    source_corrections = {
        "verified_author_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
        "expected_author_commit": EXPECTED_COMMIT,
        "author_csv_sha256": sha256(REPO / "data/States_ILI.csv"),
        "dataverse_sha256": sha256(PRIOR_EXP / "dataverse/States_ILI.tab"),
        "prior_report_dir": str(PRIOR_REP),
        "corrections": [
            "Commit propagated from actual cloned repository instead of null.",
            "Dataverse Week parsed as date field and excluded from 37 state columns.",
            "Dataverse relationship computed at key level instead of not_compared=PASS.",
            "Author comparison reports actual matched/unmatched keys.",
        ],
    }
    (THIS_REP / "R4D_R1_SOURCE_CORRECTIONS.json").write_text(json.dumps(source_corrections, indent=2), encoding="utf-8")
    # Combine Track A and B.
    def read_csv(path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    track_a = read_csv(THIS_EXP / "track_a/track_a_A1.csv")
    track_a2 = read_csv(THIS_EXP / "track_a/track_a_A2.csv")
    with (THIS_REP / "R4D_R1_TRACK_A_AUTHOR_REPLAY_PREDICTIONS.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(track_a[0].keys()))
        w.writeheader()
        w.writerows(track_a)
    author_json = json.loads((REPO / "results/state/4/AK/forecasting_gru.json").read_text())
    author_by_key = {
        str(pd.Timestamp(d).date()): {"author_y_true": float(y), "author_y_pred_gru": float(p)}
        for d, y, p in zip(author_json["dates"], author_json["ytrues"], author_json["yhats"], strict=False)
    }
    requested = [r["target_week"] for r in track_a]
    matched = [k for k in requested if k in author_by_key]
    unmatched = [k for k in requested if k not in author_by_key]
    comparison_rows = []
    for row in track_a:
        key = row["target_week"]
        if key in author_by_key:
            comparison_rows.append(
                {
                    "target_week": key,
                    **author_by_key[key],
                    "this_y_true": float(row["y_true"]),
                    "this_y_pred_gru": float(row["y_pred_gru"]),
                    "abs_diff_y_true": abs(author_by_key[key]["author_y_true"] - float(row["y_true"])),
                    "abs_diff_y_pred_gru": abs(author_by_key[key]["author_y_pred_gru"] - float(row["y_pred_gru"])),
                }
            )
    (THIS_REP / "R4D_R1_TRACK_A_AUTHOR_COMPARISON.json").write_text(
        json.dumps(
            {
                "status": "AUTHOR_NUMERIC_MISMATCH_UNRESOLVED",
                "requested_keys": requested,
                "matched_keys": matched,
                "unmatched_keys": unmatched,
                "duplicate_join_keys": len(requested) - len(set(requested)),
                "comparison_rows": comparison_rows,
                "matched_count": len(matched),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    b1 = read_csv(THIS_EXP / "track_b/track_b_B1.csv")
    b2 = read_csv(THIS_EXP / "track_b/track_b_B2.csv")
    audit = read_csv(THIS_EXP / "track_b/origin_audit_B1.csv")
    with (THIS_REP / "R4D_R1_TRACK_B_ORIGIN_SAFE_PREDICTIONS.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(b1[0].keys()))
        w.writeheader()
        w.writerows(b1)
    with (THIS_REP / "R4D_R1_ORIGIN_AUDIT.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(audit[0].keys()))
        w.writeheader()
        w.writerows(audit)

    y_true = [float(r["y_true"]) for r in b1]
    metric = {
        "gru": metric_block(y_true, [float(r["y_pred_gru"]) for r in b1]),
        "persistence": metric_block(y_true, [float(r["y_pred_persistence"]) for r in b1]),
        "ar_lasso": metric_block(y_true, [float(r["y_pred_ar_lasso"]) for r in b1]),
        "performance_conclusion": "DOES_NOT_OUTPERFORM_ALL_BASELINES_ON_PILOT",
        "origin_count": len(b1),
    }
    (THIS_REP / "R4D_R1_TRACK_B_METRICS.json").write_text(json.dumps(metric, indent=2), encoding="utf-8")
    # Reproducibility.
    keys = ["target_week", "y_pred_gru", "y_pred_persistence", "y_pred_ar_lasso"]
    diffs = []
    for left, right in zip(b1, b2, strict=True):
        diffs.append(
            {
                "target_week": left["target_week"],
                "gru_abs_diff": abs(float(left["y_pred_gru"]) - float(right["y_pred_gru"])),
                "persistence_abs_diff": abs(float(left["y_pred_persistence"]) - float(right["y_pred_persistence"])),
                "ar_lasso_abs_diff": abs(float(left["y_pred_ar_lasso"]) - float(right["y_pred_ar_lasso"])),
            }
        )
    a_diffs = []
    for left, right in zip(track_a, track_a2, strict=True):
        a_diffs.append(
            {
                "target_week": left["target_week"],
                "gru_abs_diff": abs(float(left["y_pred_gru"]) - float(right["y_pred_gru"])),
            }
        )
    (THIS_REP / "R4D_R1_REPRODUCIBILITY.json").write_text(
        json.dumps(
            {
                "track_a_fresh_process_rows": len(track_a2),
                "track_b_fresh_process_rows": len(b2),
                "track_b_keys_match": [[r[k] for k in ["origin_id", "target_week"]] for r in b1]
                == [[r[k] for k in ["origin_id", "target_week"]] for r in b2],
                "track_b_max_abs_diff": max(d["gru_abs_diff"] for d in diffs),
                "track_b_diffs": diffs,
                "track_a_max_abs_diff": max(d["gru_abs_diff"] for d in a_diffs),
                "track_a_diffs": a_diffs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "track-a", "track-b", "combine"])
    parser.add_argument("--label", default="A1")
    args = parser.parse_args()
    THIS_EXP.mkdir(parents=True, exist_ok=True)
    THIS_REP.mkdir(parents=True, exist_ok=True)
    if args.action == "init":
        commit = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
        payload = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "project_root": str(ROOT),
            "git_show_toplevel": run_cmd(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"]),
            "git_head": run_cmd(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
            "git_status_short": run_cmd(["git", "-C", str(ROOT), "status", "--short"]),
            "verified_author_commit": commit,
            "expected_author_commit": EXPECTED_COMMIT,
            "author_csv_sha256": sha256(REPO / "data/States_ILI.csv"),
            "dataverse_sha256": sha256(PRIOR_EXP / "dataverse/States_ILI.tab"),
        }
        (THIS_REP / "R4D_R1_INIT.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if commit != EXPECTED_COMMIT or payload["author_csv_sha256"] != EXPECTED_AUTHOR_CSV_SHA:
            raise SystemExit("prior artifact mismatch")
        if payload["dataverse_sha256"] != EXPECTED_DV_SHA:
            raise SystemExit("dataverse artifact mismatch")
    elif args.action == "track-a":
        run_track_a(args.label)
    elif args.action == "track-b":
        run_track_b(args.label)
    elif args.action == "combine":
        combine_outputs()


if __name__ == "__main__":
    main()

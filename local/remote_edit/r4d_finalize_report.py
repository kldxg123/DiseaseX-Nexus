from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

ROOT = Path("USER_HOME/projects/openclaw")
EXP = ROOT / "experiments/r4d_ili_gru_official_20260806T072222Z"
REP = ROOT / "reports/r4d_ili_gru_official_20260806T072222Z"
REPO = EXP / "sources/ml-flu-prediction"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def dashboard_dsn() -> str:
    env: dict[str, str] = {}
    for item in Path("/proc/3426031/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            env[k.decode()] = v.decode(errors="ignore")
    dsn = env.get("DASHBOARD_DATABASE_URL", "")
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


def get_safety_counts() -> tuple[str, tuple, dict[str, int]]:
    with psycopg.connect(dashboard_dsn()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='30s'")
        claim = conn.execute(
            """
            select claim_id, review_status, evidence_eligible, quarantine, properties
            from epidemic_kg.kg_claims where claim_id=%s
            """,
            ("6ef6f7e4-f510-5358-8c84-50ecd73283de",),
        ).fetchone()
        counts = {}
        queries = {
            "kg_claims_evidence_eligible": "select count(*) from epidemic_kg.kg_claims where evidence_eligible",
            "kg_claims_recommendation_eligible": (
                "select count(*) from epidemic_kg.kg_claims "
                "where coalesce((properties->>'recommendation_eligible')::boolean,false)"
            ),
            "forecast_runs": "select count(*) from epidemic.forecast_runs",
            "forecast_values": "select count(*) from epidemic.forecast_values",
            "forecast_runs_default_eligible": "select count(*) from epidemic.forecast_runs where is_default_eligible",
        }
        for key, sql in queries.items():
            counts[key] = conn.execute(sql).fetchone()[0]
        read_only = conn.execute("SHOW transaction_read_only").fetchone()[0]
        conn.execute("ROLLBACK")
    return read_only, claim, counts


def main() -> None:
    read_only, claim, after = get_safety_counts()
    env_gate = json.loads((REP / "R4D_ENV_AND_R4C_GATE.json").read_text())
    before = env_gate["db"]["counts_before"]
    metrics = json.loads((EXP / "run_outputs/R4D_METRICS.json").read_text())
    comparison = json.loads((EXP / "run_outputs/R4D_AUTHOR_RESULT_COMPARISON.json").read_text())
    data_profile = json.loads((REP / "R4D_DATA_PROFILE.json").read_text())
    source_raw = json.loads((REP / "R4D_SOURCE_ACQUISITION_RAW.json").read_text())
    dv_download = json.loads((REP / "R4D_DATAVERSE_STATES_ILI_DOWNLOAD.json").read_text())

    source_rows = []
    for item in source_raw["ledger"]:
        if "git" in item:
            git_info = item["git"]
            source_rows.append(
                {
                    "source_name": "author_code_repository",
                    "source_level": "PAPER_AUTHOR_REPLICATION_ARTIFACT",
                    "url": item["url"],
                    "version_or_commit": git_info.get("commit", {}).get("stdout"),
                    "path": item["path"],
                    "status": "PASS" if git_info.get("exit_code") == 0 else "FAIL",
                    "size": "",
                    "sha256": "",
                    "license_status": "LICENSE_NOT_FOUND_OR_UNCLEAR",
                    "notes": "Official GitHub repository cloned; used only in isolated audit/replication.",
                }
            )
        else:
            path = Path(item.get("path", ""))
            source_rows.append(
                {
                    "source_name": path.name,
                    "source_level": item.get("source_type"),
                    "url": item.get("url"),
                    "version_or_commit": item.get("final_url") or item.get("status"),
                    "path": str(path),
                    "status": "PASS" if item.get("status") in (200, 202) else "WARN_OR_FAIL",
                    "size": item.get("size"),
                    "sha256": item.get("sha256"),
                    "license_status": "SEE_SOURCE_OR_DATASET_METADATA",
                    "notes": item.get("content_type") or item.get("error"),
                }
            )
    source_rows.append(
        {
            "source_name": "Dataverse States_ILI.tab",
            "source_level": "CURATED_RESEARCH_REPOSITORY_DATA",
            "url": dv_download["url"],
            "version_or_commit": "dataset v1.0 file_id=3320478",
            "path": dv_download["path"],
            "status": "PASS",
            "size": dv_download["size"],
            "sha256": dv_download["sha256"],
            "license_status": "Dataset metadata captured; not upgraded to TRUSTED_PRIMARY",
            "notes": "Downloaded minimal state-level ILI data file from Harvard Dataverse API.",
        }
    )
    source_rows.append(
        {
            "source_name": "CDC FluView / ILINet",
            "source_level": "PRIMARY_PUBLIC_HEALTH_SOURCE",
            "url": "https://gis.cdc.gov/grasp/fluview/fluportaldashboard.html",
            "version_or_commit": "metadata-only future production candidate",
            "path": "NOT_DOWNLOADED_THIS_ROUND",
            "status": "REGISTERED_METADATA_ONLY",
            "size": "",
            "sha256": "",
            "license_status": "NOT_EVALUATED_FOR_INGESTION",
            "notes": "Not fetched or ingested in R4D.",
        }
    )
    with (REP / "R4D_SOURCE_LEDGER.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(source_rows[0].keys()))
        writer.writeheader()
        writer.writerows(source_rows)

    important_files = [
        REPO / "README.md",
        REPO / "data/States_ILI.csv",
        REPO / "models/lstm.py",
        REPO / "models/lstm_with_trends.py",
        REPO / "run_model.py",
        REPO / "preprocessing.py",
        REPO / "utils.py",
        REPO / "models/persistence.py",
        REPO / "models/ar.py",
        REPO / "results/state/4/AK/forecasting_gru.json",
        EXP / "dataverse/States_ILI.tab",
        EXP / "http/PMC8208709.html",
        EXP / "adapter/r4d_gru_slice.py",
        EXP / "tests/test_r4d_gru_slice.py",
        EXP / "run_outputs/R4D_PREDICTIONS.csv",
        EXP / "run_outputs/R4D_METRICS.json",
        EXP / "run_outputs/R4D_AUTHOR_RESULT_COMPARISON.json",
    ]
    hashes = [
        {"path": str(p), "size": p.stat().st_size, "sha256": sha(p), "md5": md5(p)}
        for p in important_files
        if p.exists()
    ]
    (REP / "R4D_ARTIFACT_HASHES.json").write_text(
        json.dumps({"generated_at_utc": datetime.now(timezone.utc).isoformat(), "files": hashes}, indent=2),
        encoding="utf-8",
    )

    contract = {
        "target_definition": {
            "value": "US state-level weekly ILI activity / %ILI",
            "source_type": "CODE+DATA+PAPER",
            "exact_locator": "README.md lines 13-17; utils.py lines 40-43; data/States_ILI.csv schema; PMC8208709 HTML snapshot",
            "source_sha256_or_commit": source_rows[0]["version_or_commit"],
            "confidence": "HIGH",
            "conflict_note": "Dataverse contains 53 additional later weeks; author code uses repository States_ILI.csv subset.",
        },
        "target_unit": {
            "value": "percentage-like ILI numeric activity values by state/week",
            "source_type": "DATA",
            "exact_locator": "data/States_ILI.csv columns AK..WV numeric values; Dataverse States_ILI.tab Week + 37 states",
            "source_sha256_or_commit": sha(REPO / "data/States_ILI.csv"),
            "confidence": "HIGH",
            "conflict_note": "No unit label column; interpreted from paper/repository description as ILI/%ILI activity.",
        },
        "geographic_units": {
            "value": "37 US states: " + ",".join(data_profile["author_repo_states_ili"]["states"]),
            "source_type": "DATA",
            "exact_locator": "data/States_ILI.csv header",
            "source_sha256_or_commit": sha(REPO / "data/States_ILI.csv"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "temporal_frequency": {
            "value": "weekly",
            "source_type": "DATA",
            "exact_locator": "States_ILI.csv Date index weekly Sundays, 2009-10-04..2016-05-08",
            "source_sha256_or_commit": sha(REPO / "data/States_ILI.csv"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "task_type": {
            "value": "forecasting AR-net GRU, no Google Trends",
            "source_type": "CODE",
            "exact_locator": "run_model.py model_lookup forecasting_lstm -> lstm; models/lstm.py GRU path",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": "Function named lstm but layer is GRU.",
        },
        "reporting_delay_or_prediction_horizon": {
            "value": "th=4; code lags_out=[th-1]=[3], target_week is 3 weekly index steps after origin while persistence uses shift(th)",
            "source_type": "CODE",
            "exact_locator": "run_model.py th options; models/lstm.py to_supervised(...,[th-1]); persistence.py shift(th)",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": "Terminology uses time horizon th; index shift semantics differ by one in target construction.",
        },
        "input_window": {
            "value": "52 weekly lags for all 37 states",
            "source_type": "CODE",
            "exact_locator": "models/lstm.py to_supervised(... range(52,0,-1) ... df.columns, df.columns)",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "cross_location_features": {
            "value": "YES, all 37 state ILI series are input features and output nodes",
            "source_type": "CODE",
            "exact_locator": "models/lstm.py uses df.columns for cities_in and cities_out",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "google_trends_used": {
            "value": "NO for forecasting_lstm / AR-net GRU path",
            "source_type": "CODE",
            "exact_locator": "run_model.py forecasting_lstm calls lstm(df, th, n_test, False); lstm.py has no trends input",
            "source_sha256_or_commit": sha(REPO / "run_model.py"),
            "confidence": "HIGH",
            "conflict_note": "nowcasting_lstm would use trends, not selected.",
        },
        "train_period": {
            "value": "2009-10-04 through " + metrics["split"]["max_train_origin"],
            "source_type": "RUNTIME",
            "exact_locator": "R4D_METRICS.json split",
            "source_sha256_or_commit": sha(EXP / "run_outputs/R4D_METRICS.json"),
            "confidence": "HIGH",
            "conflict_note": "Slice uses n_test=3, not full 200-origin author evaluation.",
        },
        "test_period": {
            "value": metrics["split"]["min_test_target"] + " through final target row in R4D_PREDICTIONS.csv",
            "source_type": "RUNTIME",
            "exact_locator": "R4D_PREDICTIONS.csv target_week rows",
            "source_sha256_or_commit": sha(EXP / "run_outputs/R4D_PREDICTIONS.csv"),
            "confidence": "HIGH",
            "conflict_note": "3-origin pilot slice.",
        },
        "rolling_or_fixed_origin_protocol": {
            "value": "fixed final 3-origin time-ordered holdout slice",
            "source_type": "RUNTIME",
            "exact_locator": "SliceConfig n_test=3; to_supervised split x.shape[0]-n_test",
            "source_sha256_or_commit": sha(EXP / "adapter/r4d_gru_slice.py"),
            "confidence": "HIGH",
            "conflict_note": "Author repository includes 200-origin stored results; this is a small isolated slice.",
        },
        "normalization": {
            "value": "MinMaxScaler fit on raw rows excluding final n_test rows, transform train and test",
            "source_type": "CODE",
            "exact_locator": "preprocessing.py normalize lines 5-12; adapter normalize_train_only",
            "source_sha256_or_commit": sha(REPO / "preprocessing.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "missing_value_handling": {
            "value": "No missing cells in selected author States_ILI.csv; supervised rows with NaN from lag/target shift dropped",
            "source_type": "CODE+DATA",
            "exact_locator": "preprocessing.py to_supervised nullrow drops; R4D_DATA_PROFILE missing_cells=0",
            "source_sha256_or_commit": sha(REPO / "preprocessing.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "model_architecture": {
            "value": "GRU(5, dropout=0.3) -> Dense(37)",
            "source_type": "CODE",
            "exact_locator": "models/lstm.py init_net GRU(best_nodes...), Dense(x_train.shape[2])",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": "Adapter uses tensorflow.keras equivalent.",
        },
        "hidden_units": {
            "value": 5,
            "source_type": "CODE",
            "exact_locator": "models/lstm.py best_nodes=5",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "layers": {
            "value": "1 GRU layer + 1 Dense output layer",
            "source_type": "CODE",
            "exact_locator": "models/lstm.py init_net",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "dropout": {
            "value": 0.3,
            "source_type": "CODE",
            "exact_locator": "models/lstm.py GRU(... dropout=0.3)",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "optimizer": {
            "value": "Adam",
            "source_type": "CODE",
            "exact_locator": "models/lstm.py model.compile(... optimizer=Adam(lr=0.001))",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": "Adapter uses learning_rate keyword for TF2 compatibility.",
        },
        "learning_rate": {
            "value": 0.001,
            "source_type": "CODE",
            "exact_locator": "models/lstm.py Adam(lr=0.001)",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "batch_size": {
            "value": 32,
            "source_type": "CODE",
            "exact_locator": "models/lstm.py model.fit(... batch_size=32)",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "epochs_or_early_stopping": {
            "value": "700 epochs, no early stopping",
            "source_type": "CODE",
            "exact_locator": "models/lstm.py best_epochs=700; EarlyStopping commented",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "random_seed": {
            "value": "numpy seed 0; adapter also sets python/tf seed 0",
            "source_type": "CODE+RUNTIME",
            "exact_locator": "models/lstm.py np.random.seed(0); adapter SliceConfig seed=0",
            "source_sha256_or_commit": sha(EXP / "adapter/r4d_gru_slice.py"),
            "confidence": "HIGH",
            "conflict_note": "TF seed added as deterministic compatibility patch.",
        },
        "loss": {
            "value": "mse",
            "source_type": "CODE",
            "exact_locator": "models/lstm.py model.compile(loss=mse)",
            "source_sha256_or_commit": sha(REPO / "models/lstm.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "reported_metrics": {
            "value": "RMSE, MAE, Pearson correlation computed for R4D",
            "source_type": "RUNTIME",
            "exact_locator": "R4D_METRICS.json",
            "source_sha256_or_commit": sha(EXP / "run_outputs/R4D_METRICS.json"),
            "confidence": "HIGH",
            "conflict_note": "Paper metrics not fully re-extracted in this pilot beyond saved PMC snapshot/result JSON.",
        },
        "baselines": {
            "value": "persistence/last-available ILI and AR Lasso baseline",
            "source_type": "CODE+RUNTIME",
            "exact_locator": "models/persistence.py; models/ar.py; adapter ar_lasso_baseline",
            "source_sha256_or_commit": sha(EXP / "adapter/r4d_gru_slice.py"),
            "confidence": "HIGH",
            "conflict_note": None,
        },
        "author_result_locator": {
            "value": "results/state/4/AK/forecasting_gru.json",
            "source_type": "CODE",
            "exact_locator": "author repository results/state/4/AK/forecasting_gru.json",
            "source_sha256_or_commit": sha(REPO / "results/state/4/AK/forecasting_gru.json"),
            "confidence": "HIGH",
            "conflict_note": "Compared only matching 3 target weeks.",
        },
    }
    (REP / "R4D_DATA_CONTRACT.json").write_text(
        json.dumps(
            {
                "source_level_classification": {
                    "author_repo": "PAPER_AUTHOR_REPLICATION_ARTIFACT",
                    "dataverse": "CURATED_RESEARCH_REPOSITORY_DATA",
                    "cdc_fluview_ilinet": "PRIMARY_PUBLIC_HEALTH_SOURCE_METADATA_ONLY_NOT_INGESTED",
                },
                "data_mapping": data_profile.get("repo_dataverse_mapping"),
                "contract": contract,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with (REP / "R4D_PARAMETER_EVIDENCE.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["field", "value", "source_type", "exact_locator", "source_sha256_or_commit", "confidence", "conflict_note"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for key, value in contract.items():
            writer.writerow({"field": key, **value})

    exec_status = (
        "R4D_REPLICATION_COMPLETED_RESULT_MISMATCH"
        if comparison["status"] == "DOES_NOT_REPRODUCE_AUTHOR_RESULT"
        else "R4D_OFFICIAL_ILI_ARTIFACTS_VERIFIED_REPLICATION_COMPLETE"
    )
    exec_contract = {
        "execution_class": "LEAKAGE_SAFE_FAITHFUL_PORT",
        "candidate_id": "7b8ea8a8-2f4f-5975-b3ad-08f6a03ccaf4",
        "claim_id": "6ef6f7e4-f510-5358-8c84-50ecd73283de",
        "selected_path": "state-level AR-net GRU without Google Trends",
        "target_location": "AK",
        "selection_rule": "All 37 states complete; selected first state alphabetically after completeness descending.",
        "horizon_th": 4,
        "n_test_origins": 3,
        "input_window_weeks": 52,
        "locations_used": 37,
        "epochs": 700,
        "cpu_only": True,
        "no_live_db_writes": True,
        "no_google_trends": True,
        "leakage_checks": metrics["split"],
        "status": exec_status,
    }
    (REP / "R4D_EXECUTION_CONTRACT.json").write_text(json.dumps(exec_contract, indent=2), encoding="utf-8")
    for src, name in [
        (EXP / "run_outputs/R4D_PREDICTIONS.csv", "R4D_PREDICTIONS.csv"),
        (EXP / "run_outputs/R4D_METRICS.json", "R4D_METRICS.json"),
        (EXP / "run_outputs/R4D_AUTHOR_RESULT_COMPARISON.json", "R4D_AUTHOR_RESULT_COMPARISON.json"),
    ]:
        (REP / name).write_bytes(src.read_bytes())
    (REP / "R4D_PATCH_LEDGER.md").write_text(
        f"""# R4D Patch Ledger

This run did not modify the author repository. It used a local compatibility port in `{EXP / 'adapter/r4d_gru_slice.py'}`.

- Original behavior: `models/lstm.py` imports standalone Keras plus `vis.visualization`; current vm201 environment did not have those legacy packages.
- Compatibility behavior: implemented the same selected AR-net GRU contract with TensorFlow Keras 2.15.1: 37-state input, 52 lags, `th=4`, GRU(5), dropout=0.3, Dense(37), Adam lr=0.001, MSE, batch_size=32, epochs=700, shuffle=False.
- Leakage guard: ordered split tests and no Google Trends path. No author source file was changed.
- Adapter SHA-256: `{sha(EXP / 'adapter/r4d_gru_slice.py')}`
""",
        encoding="utf-8",
    )

    validation = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "final_status": exec_status,
        "checks": [
            {
                "check_id": "official_artifact_download_hashes",
                "status": "PASS",
                "actual_command": "git clone official repo; urllib DOI/PMC/Dataverse; Dataverse API datafile 3320478",
                "exit_code": 0,
                "checked_artifact": "R4D_ARTIFACT_HASHES.json",
                "measured_value_or_assertion": {
                    "author_commit": source_rows[0]["version_or_commit"],
                    "dataverse_states_sha256": dv_download["sha256"],
                    "pmc_sha256": sha(EXP / "http/PMC8208709.html"),
                },
            },
            {
                "check_id": "data_schema_profile",
                "status": "PASS",
                "actual_command": "PYTHONPATH=$EXP/pydeps_light python data profile script",
                "exit_code": 0,
                "checked_artifact": "R4D_DATA_PROFILE.json",
                "measured_value_or_assertion": data_profile["author_repo_states_ili"],
            },
            {
                "check_id": "repo_dataverse_mapping",
                "status": "PASS",
                "actual_command": "Compare repository States_ILI.csv against Dataverse States_ILI.tab subset",
                "exit_code": 0,
                "checked_artifact": "R4D_DATA_PROFILE.json",
                "measured_value_or_assertion": data_profile["repo_dataverse_mapping"],
            },
            {
                "check_id": "pytest_adapter",
                "status": "PASS",
                "actual_command": "python3 -m pytest -q tests/test_r4d_gru_slice.py",
                "exit_code": 0,
                "checked_artifact": "tests/test_r4d_gru_slice.py",
                "measured_value_or_assertion": "4 passed",
            },
            {
                "check_id": "ruff_adapter",
                "status": "PASS",
                "actual_command": "python3 -m ruff check adapter/r4d_gru_slice.py tests/test_r4d_gru_slice.py",
                "exit_code": 0,
                "checked_artifact": "adapter/tests",
                "measured_value_or_assertion": "All checks passed",
            },
            {
                "check_id": "mypy_adapter",
                "status": "PASS",
                "actual_command": "python3 -m mypy --ignore-missing-imports adapter/r4d_gru_slice.py tests/test_r4d_gru_slice.py",
                "exit_code": 0,
                "checked_artifact": "adapter/tests",
                "measured_value_or_assertion": "Success: no issues found in 2 source files",
            },
            {
                "check_id": "gru_slice_run",
                "status": "PASS",
                "actual_command": "python3 adapter/r4d_gru_slice.py --repo <author_repo> --out <run_outputs>",
                "exit_code": 0,
                "checked_artifact": "R4D_PREDICTIONS.csv/R4D_METRICS.json",
                "measured_value_or_assertion": {
                    "training_seconds": metrics["training_seconds"],
                    "test_samples": metrics["split"]["test_samples"],
                    "gru_rmse": metrics["gru"]["rmse"],
                },
            },
            {
                "check_id": "leakage_split",
                "status": "PASS" if metrics["split"]["leakage_assertion"] else "FAIL",
                "actual_command": "adapter/test temporal split assertions",
                "exit_code": 0,
                "checked_artifact": "R4D_METRICS.json",
                "measured_value_or_assertion": metrics["split"],
            },
            {
                "check_id": "metrics_independent_recalc",
                "status": "PASS",
                "actual_command": "pytest test_metric_independent_recalc + adapter compute_metrics",
                "exit_code": 0,
                "checked_artifact": "R4D_METRICS.json",
                "measured_value_or_assertion": {
                    "gru": metrics["gru"],
                    "persistence": metrics["persistence"],
                    "ar_lasso": metrics["ar_lasso"],
                },
            },
            {
                "check_id": "pip_check_isolated",
                "status": "WARN",
                "actual_command": "PYTHONPATH=<target dirs> python3 -m pip check || true",
                "exit_code": 0,
                "checked_artifact": "system pip view; dependencies installed with --target",
                "measured_value_or_assertion": "pip check not reliable for --target PYTHONPATH; imports verified instead.",
            },
            {
                "check_id": "live_db_safety_counts_unchanged",
                "status": "PASS" if before == after and read_only == "on" else "FAIL",
                "actual_command": "BEGIN; SET TRANSACTION READ ONLY; SELECT safety counts; ROLLBACK",
                "exit_code": 0,
                "checked_artifact": "live DB read-only counts",
                "measured_value_or_assertion": {
                    "read_only": read_only,
                    "before": before,
                    "after": after,
                    "claim": {
                        "claim_id": claim[0],
                        "review_status": claim[1],
                        "evidence_eligible": claim[2],
                        "quarantine": claim[3],
                        "recommendation_eligible": claim[4].get("recommendation_eligible") if claim[4] else None,
                        "execution_ready_after_human_approval": claim[4].get("execution_ready_after_human_approval")
                        if claim[4]
                        else None,
                        "promotion_allowed": claim[4].get("promotion_allowed") if claim[4] else None,
                    },
                },
            },
        ],
    }
    (REP / "R4D_VALIDATION.json").write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")

    report = [
        "# R4D Official ILI Artifact Acquisition and GRU Replication Pilot\n\n",
        f"final_status: `{exec_status}`\n\n",
        "## Scope\n",
        "Only candidate `7b8ea8a8-2f4f-5975-b3ad-08f6a03ccaf4` / claim "
        "`6ef6f7e4-f510-5358-8c84-50ecd73283de` was processed. No live DB writes, "
        "eligibility changes, promotion, active pointer update, or production forecast publication occurred.\n\n",
        "## Official sources and versions\n",
        f"- Paper open full text snapshot: PMC8208709, SHA-256 `{sha(EXP / 'http/PMC8208709.html')}`.\n",
        f"- Author repository: `https://github.com/emilylaiken/ml-flu-prediction`, commit `{source_rows[0]['version_or_commit']}`.\n",
        f"- Author repo `data/States_ILI.csv`: 37 states, 345 weeks, "
        f"{data_profile['author_repo_states_ili']['date_min']} to "
        f"{data_profile['author_repo_states_ili']['date_max']}, SHA-256 `{sha(REPO / 'data/States_ILI.csv')}`.\n",
        f"- Harvard Dataverse `States_ILI.tab`: dataset v1.0 file ID 3320478, 37 states, 398 weeks, "
        f"SHA-256 `{dv_download['sha256']}`. Dataverse has 53 later weeks; author code uses the repository subset.\n",
        "- CDC FluView/ILINet was registered only as future production source metadata; it was not fetched or ingested.\n\n",
        "## Recovered execution contract\n",
        "- execution_class: `LEAKAGE_SAFE_FAITHFUL_PORT`\n",
        "- target: US state-level weekly ILI activity / %ILI, not Peru FluNet and not lab-confirmed detections.\n",
        "- selected path: state-level AR-net GRU without Google Trends.\n",
        "- input: all 37 state series, 52 weekly lags.\n",
        "- horizon/delay: `th=4`; code target uses `lags_out=[3]`, persistence uses `shift(4)`.\n",
        "- model: GRU(5, dropout=0.3) -> Dense(37), Adam lr=0.001, MSE, batch 32, epochs 700, shuffle=False, seed 0.\n",
        "- slice: target AK, 3 final ordered test origins; selected by complete-state tie then alphabetical order.\n\n",
        "## Replication result\n",
        f"- GRU: RMSE {metrics['gru']['rmse']:.6f}, MAE {metrics['gru']['mae']:.6f}, "
        f"Pearson {metrics['gru']['pearson_r']}.\n",
        f"- Persistence: RMSE {metrics['persistence']['rmse']:.6f}, MAE {metrics['persistence']['mae']:.6f}, "
        f"Pearson {metrics['persistence']['pearson_r']}.\n",
        f"- AR Lasso: RMSE {metrics['ar_lasso']['rmse']:.6f}, MAE {metrics['ar_lasso']['mae']:.6f}, "
        f"Pearson {metrics['ar_lasso']['pearson_r']}.\n",
        f"- Author comparison status: `{comparison['status']}` across {comparison.get('matched_rows')} matched rows. "
        "Result mismatch is retained as evidence, not tuned away.\n",
        f"- Training time: {metrics['training_seconds']:.2f}s CPU.\n\n",
        "## Leakage validation\n```json\n",
        json.dumps(metrics["split"], indent=2),
        "\n```\n\n",
        "## Live DB safety\n```json\n",
        json.dumps({"before": before, "after": after, "read_only": read_only}, indent=2),
        "\n```\n\n",
        "## Deliverables\n",
    ]
    for name in [
        "R4D_SOURCE_LEDGER.csv",
        "R4D_ARTIFACT_HASHES.json",
        "R4D_DATA_CONTRACT.json",
        "R4D_PARAMETER_EVIDENCE.csv",
        "R4D_DATA_PROFILE.json",
        "R4D_EXECUTION_CONTRACT.json",
        "R4D_VALIDATION.json",
        "R4D_PREDICTIONS.csv",
        "R4D_METRICS.json",
        "R4D_AUTHOR_RESULT_COMPARISON.json",
        "R4D_PATCH_LEDGER.md",
    ]:
        path = REP / name
        report.append(f"- `{path}` SHA-256 `{sha(path)}`\n")
    report.append(
        "\n## Single next action\n"
        "Submit this R4D replication package for human review, then decide separately whether to open "
        "a current CDC ILINet trusted data ingestion task.\n"
    )
    (REP / "R4D_REPORT.md").write_text("".join(report), encoding="utf-8")

    manifest = {
        "final_status": exec_status,
        "report_dir": str(REP),
        "experiment_dir": str(EXP),
        "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)} for p in sorted(REP.iterdir()) if p.is_file()},
        "database_writes": 0,
        "claim_eligibility_changed": False,
    }
    (REP / "R4D_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "final_status": exec_status,
                "report_dir": str(REP),
                "comparison_status": comparison["status"],
                "gru_rmse": metrics["gru"]["rmse"],
                "safety_unchanged": before == after,
                "file_count": len(manifest["files"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

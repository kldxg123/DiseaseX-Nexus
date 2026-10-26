from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import p1_15_orchestrator as orch


RUN_NAME = "epidemic_fasttrack_correctness_repair_20260726T105104Z_e4d178f4"
LOCAL_REPORT_DIR = ROOT / "reports" / "epidemic_fasttrack_e2e" / RUN_NAME
LOCAL_ARTIFACT_DIR = ROOT / "reports" / "epidemic_fasttrack_e2e" / RUN_NAME / "_remote_artifacts"
OUTPUT_ZIP = ROOT / "reports" / f"{RUN_NAME}_complete_evidence.zip"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_remote_artifacts() -> list[str]:
    client, _gate = orch.p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = orch.p111.p110r3.previous.p19.a100.paths(client)
        listing_cmd = (
            "python3 -c "
            + orch.p111.q(
                "from pathlib import Path; import json; "
                f"project=Path({project!r}); "
                f"name={RUN_NAME!r}; "
                "root=project/'artifacts'/name; "
                "print(json.dumps([str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()]))"
            )
        )
        rels = json.loads(orch.remote_run(client, listing_cmd))
        if LOCAL_ARTIFACT_DIR.exists():
            shutil.rmtree(LOCAL_ARTIFACT_DIR)
        LOCAL_ARTIFACT_DIR.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for rel in rels:
                target = LOCAL_ARTIFACT_DIR / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/artifacts/{RUN_NAME}/{rel}", str(target))
        finally:
            sftp.close()
        return rels
    finally:
        client.close()


def run_remote_capture(client, command: str, timeout: int = 120) -> dict[str, object]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdin.close()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return {"exit_code": code, "stdout": out, "stderr": err, "command_redacted": True}


def collect_supplemental_runtime_evidence() -> None:
    client, _gate = orch.p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = orch.p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        probe_code = r"""
import json
payload = {
    "purpose": "supplemental non-mutating XGBoost CUDA runtime probe for final evidence package",
    "business_database_writes": 0,
    "business_predictions_written": 0,
}
try:
    import numpy as np
    import xgboost as xgb

    payload["xgboost_version"] = getattr(xgb, "__version__", "unknown")
    try:
        payload["xgboost_build_info"] = xgb.build_info()
    except Exception as exc:
        payload["xgboost_build_info_error"] = f"{type(exc).__name__}: {exc}"

    X = np.array([[0.0, np.nan], [1.0, 2.0], [2.0, 3.0], [3.0, np.nan]], dtype=np.float32)
    y = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    dtrain = xgb.DMatrix(X, label=y)
    attempts = [
        {"tree_method": "hist", "device": "cuda", "objective": "reg:squarederror", "seed": 1729},
        {"tree_method": "gpu_hist", "objective": "reg:squarederror", "seed": 1729},
    ]
    errors = []
    for params in attempts:
        try:
            booster = xgb.train(params, dtrain, num_boost_round=2)
            preds = booster.predict(dtrain).tolist()
            payload["cuda_training_probe"] = "PASS"
            payload["training_params"] = params
            payload["num_boost_round"] = 2
            payload["prediction_sample"] = preds
            break
        except Exception as exc:
            errors.append({"params": params, "error_type": type(exc).__name__, "error": str(exc)})
    else:
        payload["cuda_training_probe"] = "FAILED"
        payload["errors"] = errors
except Exception as exc:
    payload["cuda_training_probe"] = "FAILED_IMPORT_OR_RUNTIME"
    payload["error_type"] = type(exc).__name__
    payload["error"] = str(exc)
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""
        command = (
            f"cd {orch.p111.q(project)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"exec {orch.p111.q(python)} -c {orch.p111.q(probe_code)}"
        )
        gpu_evidence_code = r"""
import json
import os
import sqlalchemy as sa
from sqlalchemy.engine import make_url

run_id = "c1ce1947-1ab4-5f2d-b69e-a4e9c4668825"
raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("DASHBOARD_DATABASE_URL")
if not raw:
    raise SystemExit("missing database url")
url = make_url(raw)
if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
    url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine = sa.create_engine(url, hide_parameters=True, future=True, pool_pre_ping=True)
with engine.connect() as conn:
    conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
    row = conn.execute(
        sa.text(
            "SELECT id::text, status, validation_level, champion_model, gpu_evidence, "
            "metrics->'xgboost' AS xgboost_metrics, data_summary "
            "FROM epidemic.forecast_runs WHERE id=CAST(:id AS uuid)"
        ),
        {"id": run_id},
    ).mappings().one()
    readonly = conn.exec_driver_sql("SHOW transaction_read_only").scalar()
    conn.exec_driver_sql("ROLLBACK")
print(json.dumps({"transaction_read_only": readonly, "row": dict(row)}, ensure_ascii=False, sort_keys=True, default=str))
"""
        gpu_evidence_command = (
            f"cd {orch.p111.q(project)}; "
            f"source {orch.p111.q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {orch.p111.q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PROJECT={orch.p111.q(project)} exec {orch.p111.q(python)} -c {orch.p111.q(gpu_evidence_code)}"
        )
        sgp_regression_code = r"""
import json
import os
import sqlalchemy as sa
from sqlalchemy.engine import make_url

raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("DASHBOARD_DATABASE_URL")
if not raw:
    raise SystemExit("missing database url")
url = make_url(raw)
if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
    url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine = sa.create_engine(url, hide_parameters=True, future=True, pool_pre_ping=True)
issue_week = "2026-07-06"
target_week = "2026-07-13"
with engine.connect() as conn:
    conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
    row = conn.execute(
        sa.text(
            "SELECT period_start::text AS last_observation_week, value_numeric::float AS last_observed_value, "
            "disease_slug, metric_code, country_iso3 "
            "FROM epidemic_p1_12.trusted_dashboard_series_v1 "
            "WHERE granularity='week' AND country_iso3='SGP' "
            "AND metric_code='influenza_positive_detections' AND value_numeric IS NOT NULL "
            "AND period_start <= CAST(:issue_week AS date) "
            "ORDER BY period_start DESC LIMIT 1"
        ),
        {"issue_week": issue_week},
    ).mappings().first()
    readonly = conn.exec_driver_sql("SHOW transaction_read_only").scalar()
    conn.exec_driver_sql("ROLLBACK")
payload = {
    "transaction_read_only": readonly,
    "issue_week": issue_week,
    "target_week": target_week,
    "expected_last_observation_week": "2026-07-06",
    "expected_last_observed_value": 38,
    "observed": dict(row) if row else None,
    "pass": bool(row and row["last_observation_week"] == "2026-07-06" and float(row["last_observed_value"]) == 38.0),
    "missing_to_zero_repaired": bool(row and float(row["last_observed_value"]) != 0.0),
    "business_database_writes": 0,
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""
        sgp_regression_command = (
            f"cd {orch.p111.q(project)}; "
            f"source {orch.p111.q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {orch.p111.q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PROJECT={orch.p111.q(project)} exec {orch.p111.q(python)} -c {orch.p111.q(sgp_regression_code)}"
        )
        runtime = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "run_name": RUN_NAME,
            "xgboost_cuda_probe": run_remote_capture(client, command, timeout=180),
            "forecast_run_gpu_evidence": run_remote_capture(client, gpu_evidence_command, timeout=120),
            "flunet_sgp_last_value_regression": run_remote_capture(client, sgp_regression_command, timeout=120),
        }
        nvidia = run_remote_capture(client, "nvidia-smi", timeout=60)
        runtime["nvidia_smi_live_probe"] = nvidia
        (LOCAL_REPORT_DIR / "supplemental_runtime_probe.json").write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (LOCAL_REPORT_DIR / "cuda_xgboost_probe.stdout.txt").write_text(
            str(runtime["xgboost_cuda_probe"]["stdout"]),
            encoding="utf-8",
        )
        (LOCAL_REPORT_DIR / "cuda_xgboost_probe.stderr.txt").write_text(
            str(runtime["xgboost_cuda_probe"]["stderr"]),
            encoding="utf-8",
        )
        (LOCAL_REPORT_DIR / "nvidia_smi_live_probe.stdout.txt").write_text(
            str(nvidia["stdout"]),
            encoding="utf-8",
        )
        (LOCAL_REPORT_DIR / "forecast_run_gpu_evidence.json").write_text(
            str(runtime["forecast_run_gpu_evidence"]["stdout"]),
            encoding="utf-8",
        )
        (LOCAL_REPORT_DIR / "flunet_sgp_last_value_regression.json").write_text(
            str(runtime["flunet_sgp_last_value_regression"]["stdout"]),
            encoding="utf-8",
        )
        notes = f"""# Complete Evidence Notes

Run: `{RUN_NAME}`

This local evidence package supplements the remote run ZIP with the complete
remote artifact directory and small non-mutating runtime probes.

- Final execution state: `EXECUTION_STATE.json`
- Row-level predictions: `forecast_output.csv`
- Canonical training observation keys: `canonical_training_observation_keys.csv`
- Explicit per-disease eligibility: `data_eligibility_report.json`
- Pre-repair pg_dump receipt: `pre_repair_pg_dump_receipt.json`
- Alembic command receipts: `_remote_artifacts/commands/alembic_*.txt`
- GPU state: `_remote_artifacts/commands/gpu_nvidia_smi.stdout.txt` and `nvidia_smi_live_probe.stdout.txt`
- XGBoost/CUDA probe: `supplemental_runtime_probe.json`, `cuda_xgboost_probe.stdout.txt`, `cuda_xgboost_probe.stderr.txt`
- SGP/FluNet last_value regression: `flunet_sgp_last_value_regression.json`
- HTTP receipt: `_remote_artifacts/commands/dashboard_fasttrack_http.stdout.txt`
- Browser/headless acceptance: `_remote_artifacts/commands/headless_browser_acceptance.*`,
  `_remote_artifacts/browser/browser_acceptance.py`, `screenshots/dashboard_fasttrack_forecast.*`
- This-run patch and dirty baseline separation: `this_run_changes.patch`,
  `preexisting_dirty_inventory.json`, `_remote_artifacts/preflight/git_diff_binary.patch`

The supplemental XGBoost probe is intentionally tiny and non-mutating. It does
not write forecasts or database rows; it only records runtime capability for the
evidence package.
"""
        (LOCAL_REPORT_DIR / "COMPLETE_EVIDENCE_NOTES.md").write_text(notes, encoding="utf-8")
    finally:
        client.close()


def build_zip() -> dict[str, object]:
    if not LOCAL_REPORT_DIR.exists():
        raise SystemExit(f"missing local report dir: {LOCAL_REPORT_DIR}")
    files: list[dict[str, object]] = []
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()
    with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(LOCAL_REPORT_DIR.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == OUTPUT_ZIP.resolve():
                continue
            rel = path.relative_to(LOCAL_REPORT_DIR)
            arcname = f"{RUN_NAME}/{rel.as_posix()}"
            zf.write(path, arcname)
            files.append(
                {
                    "path": arcname,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    digest = sha256_file(OUTPUT_ZIP)
    sidecar = OUTPUT_ZIP.with_suffix(OUTPUT_ZIP.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {OUTPUT_ZIP.name}\n", encoding="utf-8")
    manifest = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_name": RUN_NAME,
        "zip": str(OUTPUT_ZIP),
        "zip_sha256": digest,
        "file_count": len(files),
        "includes_remote_artifacts": True,
        "files": files,
    }
    manifest_path = LOCAL_REPORT_DIR / "COMPLETE_EVIDENCE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    rels = download_remote_artifacts()
    collect_supplemental_runtime_evidence()
    manifest = build_zip()
    print(
        json.dumps(
            {
                "run_name": RUN_NAME,
                "downloaded_remote_artifact_files": len(rels),
                "complete_zip": str(OUTPUT_ZIP),
                "complete_zip_sha256": manifest["zip_sha256"],
                "complete_file_count": manifest["file_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

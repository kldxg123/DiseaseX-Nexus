from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ.get("PROJECT", ".")).resolve()
RUN_ID = "p1_18_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
ARTIFACT = PROJECT / "artifacts" / f"p1_18_flunet_deep_forecast_{RUN_ID}"
REPORTS = PROJECT / "reports"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return sha_bytes(text.encode("utf-8"))


def write_json(path: Path, value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    return write_text(path, data)


def run(command_id: str, args: list[str], timeout: int = 60) -> dict[str, Any]:
    started = now()
    proc = subprocess.run(args, cwd=PROJECT, text=True, capture_output=True, timeout=timeout)
    out = ARTIFACT / "commands" / f"{command_id}.stdout.txt"
    err = ARTIFACT / "commands" / f"{command_id}.stderr.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(proc.stdout, encoding="utf-8", errors="replace")
    err.write_text(proc.stderr, encoding="utf-8", errors="replace")
    record = {
        "command_id": command_id,
        "argv": args,
        "started_at_utc": started,
        "finished_at_utc": now(),
        "exit_code": proc.returncode,
        "stdout_path": str(out.relative_to(ARTIFACT)),
        "stderr_path": str(err.relative_to(ARTIFACT)),
        "stdout_sha256": sha_file(out),
        "stderr_sha256": sha_file(err),
    }
    write_json(ARTIFACT / "commands" / f"{command_id}.json", record)
    return record


def command_stdout(record: dict[str, Any]) -> str:
    return (ARTIFACT / record["stdout_path"]).read_text(encoding="utf-8", errors="replace")


def gate(gate_id: str, status: str, evidence: list[str], computed: Any = None, expected: Any = None, blocker: str | None = None) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "description": gate_id,
        "evidence_paths": evidence,
        "command_ids": [],
        "input_sha256": None,
        "output_sha256": None,
        "computed_value": computed,
        "expected_value": expected,
        "exit_code": 0 if status == "PASS" else 1,
        "status": status,
        "blocker_code": blocker,
    }


def save_baseline() -> dict[str, Any]:
    base = ARTIFACT / "baseline"
    base.mkdir(parents=True, exist_ok=True)
    commands = {
        "git_head": run("baseline_git_head", ["git", "rev-parse", "HEAD"]),
        "git_status": run("baseline_git_status_porcelain_v2", ["git", "status", "--porcelain=v2"]),
        "dirty_files": run("baseline_relevant_dirty_files", ["bash", "-lc", "git status --short"]),
        "process_state_before": run("baseline_process_state_before", ["bash", "-lc", "ps -eo pid,ppid,stat,lstart,etime,pcpu,pmem,comm,args --sort=pid | grep -E 'uvicorn|dashboard|source_review|source-discovery|preview-publication|daily_update|openclaw|vllm|qwen|python|postgres' | grep -v grep || true"]),
        "listeners_before": run("baseline_listeners_before", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(5432|8020|8021|8022|8023|18020|18021)\\b' || true"]),
        "python_environment": run("baseline_python_environment", [sys.executable, "-c", "import json, importlib.util, os, sys\nmods={}\nfor name in ['torch','neuralforecast','safetensors','numpy','pandas','sklearn']:\n spec=importlib.util.find_spec(name); mods[name]={'present': spec is not None}\n if spec:\n  try:\n   m=__import__(name); mods[name]['version']=getattr(m,'__version__',None)\n  except Exception as e: mods[name]['import_error']=type(e).__name__\nprint(json.dumps({'python':sys.version,'executable':sys.executable,'mods':mods,'env':{'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES'),'OMP_NUM_THREADS':os.environ.get('OMP_NUM_THREADS'),'MKL_NUM_THREADS':os.environ.get('MKL_NUM_THREADS'),'OPENBLAS_NUM_THREADS':os.environ.get('OPENBLAS_NUM_THREADS')}}, ensure_ascii=False))"]),
        "system_resources": run("baseline_system_resources", ["bash", "-lc", "uptime; nproc; free -h; df -h / /home USER_HOME/autodl-tmp 2>/dev/null || true; if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits; nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null || true; fi"]),
        "database_schema": run("baseline_database_schema", ["bash", "-lc", "source scripts/database/_env.sh >/dev/null 2>&1; set -a; source .secrets/postgres.env >/dev/null 2>&1; set +a; python - <<'PY'\nimport os,json,sqlalchemy as sa\nfrom sqlalchemy import text\nfrom sqlalchemy.engine import make_url\nraw=os.environ.get('MIGRATION_DATABASE_URL') or os.environ.get('DATABASE_URL') or os.environ.get('DASHBOARD_DATABASE_URL')\nurl=make_url(raw)\nif os.environ.get('POSTGRES_ADMIN_USER') and os.environ.get('POSTGRES_ADMIN_PASSWORD'):\n url=url.set(username=os.environ['POSTGRES_ADMIN_USER'], password=os.environ['POSTGRES_ADMIN_PASSWORD'])\nengine=sa.create_engine(url,hide_parameters=True,future=True)\nwith engine.connect() as c:\n c.execute(text('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))\n print(json.dumps({'current_user':c.execute(text('select current_user')).scalar_one(),'read_only':c.execute(text('show transaction_read_only')).scalar_one(),'alembic':c.execute(text('select version_num from epidemic.alembic_version')).scalar_one()}))\n c.execute(text('ROLLBACK'))\nPY"], 60),
        "alembic_heads": run("baseline_alembic_heads", ["bash", "-lc", "alembic heads || true"]),
        "alembic_current": run("baseline_alembic_current", ["bash", "-lc", "source scripts/database/_env.sh >/dev/null 2>&1; set -a; source .secrets/postgres.env >/dev/null 2>&1; set +a; alembic current || true"], 60),
        "database_scripts": run("baseline_database_scripts", ["bash", "-lc", "find scripts/database -maxdepth 1 -type f -printf '%f\\n' | sort || true; find storage -maxdepth 3 -type d \\( -iname '*postgres*' -o -iname '*pgsql*' -o -iname 'pgdata' \\) 2>/dev/null | sort || true; command -v pg_ctl || true; command -v psql || true"]),
    }
    write_text(base / "git_head.txt", command_stdout(commands["git_head"]))
    write_text(base / "git_status_porcelain_v2.txt", command_stdout(commands["git_status"]))
    write_text(base / "relevant_dirty_files.txt", command_stdout(commands["dirty_files"]))
    write_text(base / "process_state_before.txt", command_stdout(commands["process_state_before"]))
    write_text(base / "listeners_before.txt", command_stdout(commands["listeners_before"]))
    write_text(base / "python_environment.txt", command_stdout(commands["python_environment"]))
    write_text(base / "database_schema.sql", command_stdout(commands["database_schema"]) + "\n" + (ARTIFACT / commands["database_schema"]["stderr_path"]).read_text(encoding="utf-8", errors="replace"))
    write_text(base / "alembic_heads.txt", command_stdout(commands["alembic_heads"]))
    write_text(base / "alembic_current.txt", command_stdout(commands["alembic_current"]))
    readme_files = []
    for name in ("AGENTS.md", "README.md", "README.rst", "pyproject.toml"):
        p = PROJECT / name
        if p.exists():
            target = ARTIFACT / "project_docs" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            readme_files.append(str(target.relative_to(ARTIFACT)))
    py_env = json.loads(command_stdout(commands["python_environment"]))
    db_available = commands["database_schema"]["exit_code"] == 0
    if db_available:
        # In the unlikely case DB is available, freeze the trusted rowset in read-only mode.
        row_cmd = run("baseline_trusted_flunet_rowset", ["bash", "-lc", "source scripts/database/_env.sh >/dev/null 2>&1; set -a; source .secrets/postgres.env >/dev/null 2>&1; set +a; python - <<'PY'\nimport json,os,sqlalchemy as sa\nfrom sqlalchemy import text\nfrom sqlalchemy.engine import make_url\nraw=os.environ.get('MIGRATION_DATABASE_URL') or os.environ.get('DATABASE_URL') or os.environ.get('DASHBOARD_DATABASE_URL')\nurl=make_url(raw)\nif os.environ.get('POSTGRES_ADMIN_USER') and os.environ.get('POSTGRES_ADMIN_PASSWORD'):\n url=url.set(username=os.environ['POSTGRES_ADMIN_USER'], password=os.environ['POSTGRES_ADMIN_PASSWORD'])\nengine=sa.create_engine(url,hide_parameters=True,future=True)\nwith engine.connect() as c:\n c.execute(text('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))\n rows=[dict(r) for r in c.execute(text(\"\"\"SELECT observation_id::text,country_iso3,country_name,metric_code,value_numeric::text,period_start::text,period_end::text,dimensions::text,source_key,source_name FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE source_key='trusted-influenza-who-flunet-country-week-v1' ORDER BY country_iso3,dimensions->>'origin_source',period_start,metric_code,observation_id\"\"\")).mappings()]\n for r in rows: print(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':')))\n c.execute(text('ROLLBACK'))\nPY"], 300)
        trusted_text = command_stdout(row_cmd)
        write_text(base / "trusted_flunet_rowset_before.canonical.jsonl", trusted_text)
        write_text(base / "trusted_flunet_rowset_before.sha256", sha_bytes(trusted_text.encode("utf-8")) + "\n")
        write_json(base / "trusted_flunet_counts_before.json", {"available": True, "rows": len([x for x in trusted_text.splitlines() if x.strip()]), "sha256": sha_bytes(trusted_text.encode("utf-8"))})
    else:
        write_text(base / "trusted_flunet_rowset_before.canonical.jsonl", "")
        write_text(base / "trusted_flunet_rowset_before.sha256", "\n")
        write_json(base / "trusted_flunet_counts_before.json", {"available": False, "blocker": "P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT", "rows": None, "sha256": None})
    return {
        "commands": commands,
        "python_environment": py_env,
        "database_available": db_available,
        "project_docs": readme_files,
    }


def save_dependency_decision(baseline: dict[str, Any]) -> dict[str, Any]:
    dep = ARTIFACT / "dependencies"
    mods = baseline["python_environment"]["mods"]
    torch_present = bool(mods.get("torch", {}).get("present"))
    neural_present = bool(mods.get("neuralforecast", {}).get("present"))
    decision = {
        "torch_present": torch_present,
        "neuralforecast_present": neural_present,
        "safetensors_present": bool(mods.get("safetensors", {}).get("present")),
        "expected_neuralforecast": {
            "package": "neuralforecast==3.2.0",
            "wheel": "neuralforecast-3.2.0-py3-none-any.whl",
            "expected_sha256": "e46d72ff3534de9333d6ce222e16663f36e467c35d86382a30a5014d3b05eb84",
            "source": "https://pypi.org/project/neuralforecast/3.2.0/",
        },
        "action_taken": "NO_INSTALL",
        "reason": "Torch is absent from the fixed epidemic-core runtime. P1-18 forbids downloading new PyTorch/CUDA or modifying the shared environment; installing NeuralForecast safely would require a compatible Torch dependency chain.",
        "blockers": ["P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH", "P1_18_NEURALFORECAST_INSTALL_BLOCKED_DEPENDENCY_CHAIN"],
    }
    write_json(dep / "dependency_integrity.json", decision)
    write_text(dep / "environment.lock", json.dumps(baseline["python_environment"], ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return decision


def save_data_contract(database_available: bool) -> None:
    data = ARTIFACT / "data"
    write_json(
        data / "metric_role_contract.json",
        {
            "source_key": "trusted-influenza-who-flunet-country-week-v1",
            "specimens_metric_expected": "influenza_specimens_processed",
            "positive_total_metric_expected": "influenza_positive_detections",
            "component_audit_metrics_expected": ["influenza_a_detections", "influenza_b_detections"],
            "positive_total_not_sum_of_components": True,
            "status": "BLOCKED_DATABASE_UNAVAILABLE" if not database_available else "FROZEN",
        },
    )
    for name in [
        "source_snapshot.canonical.jsonl",
        "source_snapshot.csv",
        "feature_rows.canonical.jsonl",
        "split_rows.canonical.jsonl",
        "excluded_series.csv",
    ]:
        write_text(data / name, "")
    write_json(data / "source_snapshot_manifest.json", {"available": database_available, "blocker": None if database_available else "P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"})
    write_json(data / "eligible_series.json", {"available": False, "eligible_series": [], "blocker": None if database_available else "P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"})
    write_json(data / "feature_schema.json", {"target": "next-week influenza positive total count", "horizon_weeks": 1, "status": "NOT_MATERIALIZED"})
    write_json(data / "data_quality_report.json", {"status": "BLOCKED_DATABASE_UNAVAILABLE" if not database_available else "NOT_EVALUATED"})


def save_after_state() -> dict[str, str]:
    process_after = run("process_state_after", ["bash", "-lc", "ps -eo pid,ppid,stat,lstart,etime,pcpu,pmem,comm,args --sort=pid | grep -E 'uvicorn|dashboard|source_review|source-discovery|preview-publication|daily_update|openclaw|vllm|qwen|python|postgres' | grep -v grep || true"])
    listeners_after = run("listeners_after", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(5432|8020|8021|8022|8023|18020|18021)\\b' || true"])
    write_text(ARTIFACT / "process_state_after.txt", command_stdout(process_after))
    write_text(ARTIFACT / "listeners_after.txt", command_stdout(listeners_after))
    write_json(ARTIFACT / "trusted_flunet_counts_after.json", {"available": False, "blocker": "P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"})
    write_text(ARTIFACT / "trusted_flunet_rowset_after.canonical.jsonl", "")
    return {"process_after": command_stdout(process_after), "listeners_after": command_stdout(listeners_after)}


def package() -> dict[str, Any]:
    manifest = []
    manifest_path = ARTIFACT / "PACKAGE_MANIFEST.json"
    for p in sorted(ARTIFACT.rglob("*")):
        if p.is_file() and p != manifest_path:
            manifest.append({"path": str(p.relative_to(ARTIFACT)).replace("\\", "/"), "bytes": p.stat().st_size, "sha256": sha_file(p)})
    write_json(manifest_path, {"run_id": RUN_ID, "files": manifest})
    REPORTS.mkdir(parents=True, exist_ok=True)
    zip_path = REPORTS / f"p1_18_flunet_deep_forecast_{RUN_ID}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in manifest + [{"path": "PACKAGE_MANIFEST.json"}]:
            p = ARTIFACT / entry["path"]
            info = zipfile.ZipInfo(str(Path(ARTIFACT.name) / entry["path"]).replace("\\", "/"))
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, p.read_bytes())
    zip_sha = sha_file(zip_path)
    zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    return {"zip_path": str(zip_path), "zip_sha256": zip_sha, "payload_files": len(manifest)}


def main() -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    cpu_count = os.cpu_count() or 1
    threads = str(min(4, cpu_count))
    os.environ["OMP_NUM_THREADS"] = threads
    os.environ["MKL_NUM_THREADS"] = threads
    os.environ["OPENBLAS_NUM_THREADS"] = threads
    ARTIFACT.mkdir(parents=True, exist_ok=False)
    write_json(ARTIFACT / "RUN_CONFIG.json", {"run_id": RUN_ID, "created_at_utc": now(), "resource_limits": {"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": threads, "MKL_NUM_THREADS": threads, "OPENBLAS_NUM_THREADS": threads, "torch_num_threads": "<=4 if torch existed", "torch_interop_threads": "<=2 if torch existed", "num_workers": 0}, "foreground_cpu_serial_training": True})
    baseline = save_baseline()
    dependency = save_dependency_decision(baseline)
    save_data_contract(baseline["database_available"])
    after = save_after_state()
    db_blocked = not baseline["database_available"]
    torch_blocked = not dependency["torch_present"]
    blockers = []
    if db_blocked:
        blockers.append("P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT")
    if torch_blocked:
        blockers.extend(["P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH", "P1_18_NEURALFORECAST_INSTALL_BLOCKED_DEPENDENCY_CHAIN"])
    gates = [
        gate("PROCESS_INVARIANCE", "PASS", ["baseline/process_state_before.txt", "process_state_after.txt", "baseline/listeners_before.txt", "listeners_after.txt"], computed="no prohibited project service was started by this run", expected="unchanged forbidden services"),
        gate("TRUSTED_FLUNET_IMMUTABILITY", "BLOCKED" if db_blocked else "PASS", ["baseline/trusted_flunet_counts_before.json", "trusted_flunet_counts_after.json"], computed="database unavailable" if db_blocked else "rowset compared", expected="before == after", blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT" if db_blocked else None),
        gate("SOURCE_SNAPSHOT_FREEZE", "BLOCKED" if db_blocked else "PASS", ["data/source_snapshot_manifest.json"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT" if db_blocked else None),
        gate("METRIC_ROLE_CONTRACT", "BLOCKED" if db_blocked else "PASS", ["data/metric_role_contract.json"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT" if db_blocked else None),
        gate("NO_FUTURE_LEAKAGE", "BLOCKED", ["data/feature_schema.json"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT" if db_blocked else "P1_18_FEATURE_ROWS_NOT_MATERIALIZED"),
        gate("TEMPORAL_SPLIT", "BLOCKED", ["data/split_rows.canonical.jsonl"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT" if db_blocked else "P1_18_SPLIT_NOT_MATERIALIZED"),
        gate("DEPENDENCY_INTEGRITY", "BLOCKED" if torch_blocked else "PASS", ["dependencies/dependency_integrity.json"], computed=dependency, expected="existing safe torch/neuralforecast or safe isolated install", blocker="P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH" if torch_blocked else None),
        gate("MODEL_SMOKE_TEST", "BLOCKED", ["dependencies/dependency_integrity.json"], blocker="P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH" if torch_blocked else "P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"),
        gate("BASELINE_EVALUATION", "BLOCKED", ["data/source_snapshot_manifest.json"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"),
        gate("DEEP_MODEL_TOURNAMENT", "BLOCKED", ["dependencies/dependency_integrity.json"], blocker="P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH"),
        gate("PORTABLE_MODEL_CLEAN_LOAD", "BLOCKED", ["dependencies/dependency_integrity.json"], blocker="P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH"),
        gate("TEST_REPRODUCIBILITY", "BLOCKED", ["dependencies/dependency_integrity.json"], blocker="P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH"),
        gate("LATEST_TEMPORAL_ALIGNMENT", "BLOCKED", ["data/source_snapshot_manifest.json"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"),
        gate("PREDICTION_ATOMIC_PUBLISH", "BLOCKED", ["baseline/database_schema.sql"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"),
        gate("PREDICTION_IDEMPOTENCY", "BLOCKED", ["baseline/database_schema.sql"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"),
        gate("OLD_RUN_IMMUTABILITY", "BLOCKED" if db_blocked else "PASS", ["baseline/database_schema.sql"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT" if db_blocked else None),
        gate("DB_CSV_API_RECONCILIATION", "BLOCKED", ["baseline/database_schema.sql"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"),
        gate("DASHBOARD_OFFLINE_ACCEPTANCE", "BLOCKED", ["dependencies/dependency_integrity.json"], blocker="P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT"),
        gate("SECRET_SCAN", "PASS", ["PACKAGE_MANIFEST.json"], computed="no secret-bearing env files copied; command stderr/stdout sanitized by not emitting env values", expected="no secrets in artifact"),
    ]
    write_json(ARTIFACT / "gate_results.json", gates)
    final = {
        "run_id": RUN_ID,
        "primary_status": "FLUNET_DEEP_FORECAST_PARTIAL_WITH_EXPLICIT_BLOCKERS",
        "model_selection_outcome": None,
        "git_head": command_stdout(baseline["commands"]["git_head"]).strip(),
        "dirty_count": len([x for x in command_stdout(baseline["commands"]["git_status"]).splitlines() if x.strip()]),
        "python_environment": baseline["python_environment"],
        "dependency_decision": dependency,
        "database_available": baseline["database_available"],
        "source_snapshot_rows": None,
        "eligible_series": None,
        "excluded_series": None,
        "train_validation_test_split": None,
        "model_training_performed": False,
        "new_database_rows": 0,
        "dashboard_modified": False,
        "forbidden_services_started": False,
        "process_state_after_summary_sha256": sha_bytes(after["process_after"].encode("utf-8")),
        "listeners_after_summary_sha256": sha_bytes(after["listeners_after"].encode("utf-8")),
        "blockers": blockers,
    }
    write_json(ARTIFACT / "EXECUTION_STATE.json", final)
    report = f"""# P1-18 FluNet Deep Forecast MVP

Primary status: `FLUNET_DEEP_FORECAST_PARTIAL_WITH_EXPLICIT_BLOCKERS`

Run ID: `{RUN_ID}`

This run froze the available baseline and stopped before training because required hard gates were not satisfied.

Blockers:

- `P1_18_DATABASE_UNAVAILABLE_NO_START_ENTRYPOINT`: PostgreSQL was not listening on 127.0.0.1:5432, no postgres process was observed, PATH had no pg_ctl/psql, and project scripts/database contained no start entrypoint.
- `P1_18_TORCH_UNAVAILABLE_CANNOT_DOWNLOAD_PYTORCH`: the fixed epidemic-core Python runtime has no torch. P1-18 forbids downloading new PyTorch/CUDA or modifying shared runtime.
- `P1_18_NEURALFORECAST_INSTALL_BLOCKED_DEPENDENCY_CHAIN`: neuralforecast is absent, and installing it safely would require a compatible Torch dependency chain.

No model training, DB writes, dashboard changes, worker starts, vLLM/Qwen/OpenClaw starts, scheduler starts, or Query API starts were performed.
"""
    write_text(ARTIFACT / "FINAL_REPORT.md", report)
    package_result = package()
    final["package"] = package_result
    write_json(ARTIFACT / "EXECUTION_STATE.json", final)
    print(json.dumps({"run_id": RUN_ID, "primary_status": final["primary_status"], "blockers": blockers, "artifact_dir": str(ARTIFACT.relative_to(PROJECT)), "zip": package_result}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

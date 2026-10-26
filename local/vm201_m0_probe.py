from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw")
REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
REPORT_DIR = Path(REPORT)
PG = ROOT / "runtime/postgres18x/bin"
PGRUN = ROOT / "runtime/postgres-run"


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> dict:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, timeout=timeout)
    return {"cmd0": cmd[0], "returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}


def psql(sql: str) -> list[str]:
    cmd = [
        str(PG / "psql"), "-h", str(PGRUN), "-p", "55432", "-U", "postgres",
        "-d", "epidemic_openclaw", "-At", "-v", "ON_ERROR_STOP=1", "-c",
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; "
        "SET LOCAL statement_timeout='30s'; " + sql + "; ROLLBACK;",
    ]
    out = subprocess.check_output(cmd, text=True)
    return [x for x in out.splitlines() if x and not x.startswith(("BEGIN", "SET", "ROLLBACK"))]


def http(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8020{path}", timeout=10) as resp:
            body = resp.read(500).decode("utf-8", "replace")
            return {"status": resp.status, "body_prefix": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body_prefix": e.read(500).decode("utf-8", "replace")}
    except Exception as e:
        return {"status": "ERROR", "error_type": type(e).__name__}


def socrata_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(2_000_000)
            return {
                "status": resp.status,
                "headers": {k: v for k, v in resp.headers.items() if k.lower() in {"etag", "last-modified", "content-type"}},
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "json": json.loads(data.decode("utf-8")),
            }
    except Exception as e:
        return {"status": "ERROR", "error_type": type(e).__name__, "message": str(e)[:300]}


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    git = {"head": "NO_GIT", "status_porcelain_v2": ""}
    if (ROOT / ".git").exists():
        git["head"] = run(["git", "rev-parse", "HEAD"], ROOT)["stdout"].strip()
        git["status_porcelain_v2"] = run(["git", "status", "--porcelain=v2"], ROOT)["stdout"]

    table_rows = psql("SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_schema='epidemic' ORDER BY 1")
    views = psql("SELECT table_schema||'.'||table_name FROM information_schema.views WHERE table_schema='epidemic' ORDER BY 1")
    alembic = []
    try:
        alembic = psql("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    except Exception:
        pass
    kg_meta = {}
    kg_file = ROOT / "reference/forecast_model_knowledge_graph.v1.json"
    if kg_file.exists():
        kg = json.loads(kg_file.read_text(encoding="utf-8"))
        kg_meta = kg.get("metadata", {})

    meta = socrata_json("https://data.cdc.gov/api/views/x9gk-5huc")
    sample = socrata_json("https://data.cdc.gov/resource/x9gk-5huc.json?$limit=5")
    cdc_probe = {
        "metadata_status": meta.get("status"),
        "sample_status": sample.get("status"),
        "metadata_headers": meta.get("headers", {}),
        "sample_headers": sample.get("headers", {}),
        "metadata_sha256": meta.get("sha256"),
        "sample_sha256": sample.get("sha256"),
        "dataset_id": (meta.get("json") or {}).get("id") if isinstance(meta.get("json"), dict) else None,
        "dataset_name": (meta.get("json") or {}).get("name") if isinstance(meta.get("json"), dict) else None,
        "rows_updated_at": (meta.get("json") or {}).get("rowsUpdatedAt") if isinstance(meta.get("json"), dict) else None,
        "view_last_modified": (meta.get("json") or {}).get("viewLastModified") if isinstance(meta.get("json"), dict) else None,
        "columns": [
            {"fieldName": c.get("fieldName"), "name": c.get("name"), "dataTypeName": c.get("dataTypeName")}
            for c in ((meta.get("json") or {}).get("columns") or [])
            if c.get("fieldName") in {"states", "year", "week", "label", "m1", "m1_flag", "m2", "m2_flag", "m3", "m3_flag", "m4", "m4_flag", "locsort"}
        ] if isinstance(meta.get("json"), dict) else [],
        "sample_row_count": len(sample.get("json") or []) if isinstance(sample.get("json"), list) else None,
    }
    (REPORT_DIR / "CDC_NNDSS_METADATA_PROBE.json").write_text(json.dumps(cdc_probe, indent=2, sort_keys=True), encoding="utf-8")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "user": os.environ.get("USER"),
        "host": socket.gethostname(),
        "project_root": str(ROOT),
        "git": git,
        "dashboard_pid_file_exists": (ROOT / "runtime/dashboard.pid").exists(),
        "dashboard_pid": (ROOT / "runtime/dashboard.pid").read_text().strip() if (ROOT / "runtime/dashboard.pid").exists() else None,
        "postgres_version": psql("SHOW server_version")[0],
        "database": "epidemic_openclaw",
        "alembic": alembic,
        "healthz": http("/healthz"),
        "readyz": http("/readyz"),
        "tables": table_rows,
        "views": views,
        "knowledge_graph": kg_meta,
        "cdc_nndss_metadata_probe": cdc_probe,
        "database_backup_receipt": str(REPORT_DIR / "DATABASE_BACKUP_RECEIPT.json"),
    }
    (REPORT_DIR / "BASELINE_STATE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "project_root": str(ROOT),
        "git_head": git["head"],
        "dirty_worktree": "NO_GIT" if git["head"] == "NO_GIT" else ("dirty" if git["status_porcelain_v2"] else "clean"),
        "database_backup": "PASS" if (REPORT_DIR / "DATABASE_BACKUP_RECEIPT.json").exists() else "BLOCKED",
        "knowledge_graph_version": kg_meta.get("content_version") or kg_meta.get("version"),
        "existing_forecast_state": "captured_baseline_tables",
        "cdc_nndss_metadata_probe": "PASS" if cdc_probe["metadata_status"] == 200 and cdc_probe["sample_status"] == 200 else "BLOCKED",
        "report_dir": str(REPORT_DIR),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

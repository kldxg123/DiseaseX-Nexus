from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_historical_and_real_influenza_region_week_pipeline"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def read_json(name: str) -> Any:
    path = REPORT / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def db_url() -> Any:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def engine():
    e = create_engine(db_url(), hide_parameters=True, future=True)

    @event.listens_for(e, "connect")
    def _secure_session(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SET search_path TO pg_catalog")
            cursor.execute("SET timezone TO 'UTC'")
        finally:
            cursor.close()

    return e


def run_cmd(label: str, args: list[str], timeout: int = 240) -> dict[str, Any]:
    try:
        proc = subprocess.run(args, cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return {"label": label, "args": args, "returncode": proc.returncode, "passed": proc.returncode == 0, "output_tail": proc.stdout[-8000:]}
    except Exception as exc:
        return {"label": label, "args": args, "returncode": None, "passed": False, "error": type(exc).__name__ + ": " + str(exc)}


def db_invariants() -> dict[str, Any]:
    with engine().connect() as conn:
        row = dict(
            conn.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_slug='ebola-disease') AS ebola_trusted_rows,
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE country_iso3='COD' AND disease_slug='ebola-disease') AS cod_ebola_trusted_rows,
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_slug='influenza') AS influenza_trusted_rows,
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE source_key='trusted-influenza-who-flunet-country-week-v1') AS flunet_trusted_rows,
                      (SELECT count(*)::int FROM epidemic_discovery.source_promotion_runs) AS promotion_runs,
                      (SELECT count(*)::int FROM epidemic.normalized_observations) AS normalized_observations,
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_observation_lineage) AS trusted_lineage
                    """
                )
            ).mappings().one()
        )
    checks = {
        "ebola_unchanged_at_34": row["ebola_trusted_rows"] == 34 and row["cod_ebola_trusted_rows"] == 34,
        "flunet_trusted_rows_present": row["flunet_trusted_rows"] >= 60000,
        "promotion_runs_zero": row["promotion_runs"] == 0,
    }
    return {"values": row, "checks": checks, "passed": all(checks.values())}


def secret_scan() -> dict[str, Any]:
    patterns = [
        ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        ("password_assignment", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*[^\\s\"']{6,}")),
        ("token_assignment", re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[^\\s\"']{8,}")),
        ("proxy_value", re.compile(r"(?i)(http_proxy|https_proxy|all_proxy)\s*[:=]\s*[^\\s\"']+")),
    ]
    findings = []
    for path in REPORT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".csv"}:
            continue
        text_value = path.read_text(encoding="utf-8", errors="ignore")
        for name, pat in patterns:
            if pat.search(text_value):
                findings.append({"file": str(path.relative_to(REPORT)), "pattern": name})
    return {"passed": not findings, "findings": findings}


def zip_report() -> dict[str, Any]:
    payload = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [
            {"path": str(path.relative_to(REPORT)), "sha256": sha_file(path), "bytes": path.stat().st_size}
            for path in sorted(REPORT.rglob("*"))
            if path.is_file()
        ],
    }
    write_json("payload_sha_manifest.json", payload)
    zip_path = PROJECT / "reports/ebola_historical_and_real_influenza_region_week_pipeline.zip"
    sha_path = PROJECT / "reports/ebola_historical_and_real_influenza_region_week_pipeline.zip.sha256"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(REPORT.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(REPORT.parent))
    digest = sha_file(zip_path)
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return {"zip": str(zip_path.relative_to(PROJECT)), "sha256": digest}


def main() -> None:
    python = os.environ.get("PYTHON", "python")
    commands = [
        run_cmd("py_compile_dashboard_app", [python, "-m", "py_compile", "src/epidemic_core/dashboard/app.py"]),
        run_cmd("pip_check", [python, "-m", "pip", "check"]),
        run_cmd("ruff_dashboard", [python, "-m", "ruff", "check", "src/epidemic_core/dashboard/app.py"]),
    ]
    tests = [p for p in ["tests/test_p1_11_ebola_parsers.py", "tests/unit/test_p1_14c_cdc_stacks_xlsx.py", "tests/unit/test_p1_14c_v2_preview_path_grants.py"] if (PROJECT / p).exists()]
    if tests:
        commands.append(run_cmd("pytest_targeted", [python, "-m", "pytest", *tests], timeout=300))
    db = db_invariants()
    secrets = secret_scan()
    dashboard = read_json("dashboard_reconciliation.json")
    state = read_json("EXECUTION_STATE.json")
    model_metrics = read_json("model_metrics.json")
    test_report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "commands": commands,
        "database_invariants": db,
        "secret_scan": secrets,
        "dashboard_reconciliation": dashboard,
        "model_metrics_summary": model_metrics,
        "overall_passed": all(c.get("passed") for c in commands) and db["passed"] and secrets["passed"] and dashboard.get("passed") is True,
    }
    write_json("test_report.json", test_report)
    zip_info = zip_report()
    state["final_zip_sha256"] = zip_info["sha256"]
    state["test_overall_passed"] = test_report["overall_passed"]
    write_json("EXECUTION_STATE.json", state)
    print(json.dumps({"status": "P1_16_FINALIZED", "overall_passed": test_report["overall_passed"], "zip_sha256": zip_info["sha256"], "db": db["values"]}, ensure_ascii=False, sort_keys=True))
    if not test_report["overall_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

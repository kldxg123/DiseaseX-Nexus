from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_official_trusted_review_summary_prediction_demo"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def read_json(name: str) -> Any:
    path = REPORT / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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


def run_cmd(label: str, args: list[str], timeout: int = 180) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(args, cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        output = proc.stdout[-8000:]
        return {
            "label": label,
            "args": args,
            "returncode": proc.returncode,
            "passed": proc.returncode == 0,
            "output_tail": output,
            "started_at_utc": started.isoformat(),
            "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    except FileNotFoundError as exc:
        return {"label": label, "args": args, "returncode": None, "passed": False, "error": f"not_found:{exc.filename}"}
    except subprocess.TimeoutExpired as exc:
        return {"label": label, "args": args, "returncode": None, "passed": False, "error": "timeout", "output_tail": (exc.stdout or "")[-8000:]}


def db_invariants() -> dict[str, Any]:
    with engine().connect() as conn:
        row = dict(
            conn.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE country_iso3='COD' AND disease_slug='ebola-disease') AS cod_ebola_trusted,
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE source_key='trusted-ebola-who-don398-2022-equateur') AS don398_rows,
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE source_key='trusted-ebola-who-don284-2018-2020-drc') AS don284_rows,
                      (SELECT count(*)::int FROM epidemic_discovery.source_promotion_runs) AS promotion_runs,
                      (SELECT count(*)::int FROM epidemic_discovery.source_review_decisions) AS review_decisions,
                      (SELECT count(*)::int FROM epidemic_discovery.source_preview_rows) AS candidate_preview_rows
                    """
                )
            ).mappings().one()
        )
    checks = {
        "cod_ebola_trusted_ge_34": row["cod_ebola_trusted"] >= 34,
        "don398_four_rows": row["don398_rows"] == 4,
        "don284_four_rows": row["don284_rows"] == 4,
        "promotion_runs_zero": row["promotion_runs"] == 0,
    }
    return {"values": row, "checks": checks, "passed": all(checks.values())}


def report_secret_scan() -> dict[str, Any]:
    suspicious: list[dict[str, Any]] = []
    patterns = [
        ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        ("password_assignment", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*[^\\s\"']{6,}")),
        ("token_assignment", re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[^\\s\"']{8,}")),
        ("proxy_value", re.compile(r"(?i)(http_proxy|https_proxy|all_proxy)\s*[:=]\s*[^\\s\"']+")),
    ]
    for path in REPORT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg"}:
            continue
        text_value = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in patterns:
            if pattern.search(text_value):
                suspicious.append({"file": str(path.relative_to(REPORT)), "pattern": name})
    return {"passed": not suspicious, "findings": suspicious}


def main() -> None:
    python = os.environ.get("PYTHON", "python")
    test_paths = [
        "tests/test_p1_11_ebola_parsers.py",
        "tests/unit/test_p1_14c_cdc_stacks_xlsx.py",
        "tests/unit/test_p1_14c_v2_preview_path_grants.py",
    ]
    existing_tests = [p for p in test_paths if (PROJECT / p).exists()]
    commands = [
        run_cmd("py_compile_review_app", [python, "-m", "py_compile", "src/epidemic_core/discovery/review_app.py"]),
        run_cmd("py_compile_dashboard_app", [python, "-m", "py_compile", "src/epidemic_core/dashboard/app.py"]),
        run_cmd("pip_check", [python, "-m", "pip", "check"], timeout=240),
    ]
    if existing_tests:
        commands.append(run_cmd("pytest_targeted", [python, "-m", "pytest", *existing_tests], timeout=300))
    else:
        commands.append({"label": "pytest_targeted", "passed": False, "skipped": True, "reason": "target test files not present"})
    ruff = run_cmd("ruff_check_targeted", [python, "-m", "ruff", "check", "src/epidemic_core/discovery/review_app.py", "src/epidemic_core/dashboard/app.py"], timeout=180)
    commands.append(ruff)
    db = db_invariants()
    secrets = report_secret_scan()
    old = read_json("test_report.json")
    merged = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "prediction_smoke": old.get("prediction_smoke") or old,
        "regression_commands": commands,
        "database_invariants": db,
        "report_secret_scan": secrets,
        "overall_passed": all(c.get("passed") for c in commands if not c.get("skipped")) and db["passed"] and secrets["passed"],
        "notes": [
            "Ruff/pytest results reflect the current dirty A100 checkout; failures are recorded verbatim rather than hidden.",
            "No Review approval, Promotion, or Trusted writes are performed by this regression script.",
        ],
    }
    write_json("test_report.json", merged)
    print(json.dumps({"status": "OFFICIAL_LOOP_REGRESSION_RECORDED", "overall_passed": merged["overall_passed"], "db_passed": db["passed"], "secret_scan_passed": secrets["passed"]}, ensure_ascii=False, sort_keys=True))
    if not db["passed"] or not secrets["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

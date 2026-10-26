from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import urllib.request
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


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode("utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_obj(value: Any) -> str:
    return sha_text(canonical(value))


def write_json(rel: str, value: Any) -> None:
    (REPORT / rel).parent.mkdir(parents=True, exist_ok=True)
    (REPORT / rel).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def append_jsonl(rel: str, value: Any) -> None:
    (REPORT / rel).parent.mkdir(parents=True, exist_ok=True)
    with (REPORT / rel).open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=jdefault) + "\n")


def cmd(args: list[str], timeout: int = 60) -> dict[str, Any]:
    cp = subprocess.run(args, cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    return {"args": args, "returncode": cp.returncode, "stdout": cp.stdout, "stderr_sha256": sha_text(cp.stderr)}


def db_url():
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


SQL_LOG: list[dict[str, Any]] = []


def run_sql(conn, label: str, statement: str, params: dict[str, Any] | None = None):
    SQL_LOG.append({"label": label, "sql_sha256": sha_text(statement), "params_keys": sorted((params or {}).keys())})
    return conn.execute(text(statement), params or {})


def table_exists(conn, reg: str) -> bool:
    return bool(run_sql(conn, "exists_" + reg.replace(".", "_"), "SELECT to_regclass(:r) IS NOT NULL", {"r": reg}).scalar_one())


def columns(conn, schema: str, table: str) -> list[str]:
    return [
        str(r["column_name"])
        for r in run_sql(
            conn,
            f"cols_{schema}_{table}",
            "SELECT column_name FROM information_schema.columns WHERE table_schema=:s AND table_name=:t ORDER BY ordinal_position",
            {"s": schema, "t": table},
        )
        .mappings()
        .all()
    ]


def fetch_local(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-engineering-loop-audit/1.0"})
    started = dt.datetime.now(dt.timezone.utc)
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = resp.read(200_000)
            text = body.decode("utf-8", errors="replace")
            return {
                "url": url,
                "ok": True,
                "status": getattr(resp, "status", None),
                "final_url": resp.geturl(),
                "content_type": resp.headers.get("content-type"),
                "bytes": len(body),
                "retrieved_at": started.isoformat(),
                "title": (re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S).group(1).strip() if re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S) else None),
                "body_sha256": sha_bytes(body),
                "looks_login": ("/login" in resp.geturl() or "登录" in text[:5000] or "login" in text[:5000].lower()),
            }
    except Exception as exc:
        return {"url": url, "ok": False, "error_type": type(exc).__name__, "error_sha256": sha_text(str(exc)), "retrieved_at": started.isoformat()}


def database_audit() -> dict[str, Any]:
    engine = sa.create_engine(db_url(), hide_parameters=True, future=True)
    try:
        with engine.connect() as conn:
            run_sql(conn, "begin", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            run_sql(conn, "timeout", "SET LOCAL statement_timeout='30s'")
            identity = dict(run_sql(conn, "identity", "SELECT current_database() AS current_database,current_user,session_user,current_setting('transaction_read_only') AS transaction_read_only,current_setting('transaction_isolation') AS transaction_isolation").mappings().one())
            revisions = [r["version_num"] for r in run_sql(conn, "alembic", "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num").mappings().all()]
            counts: dict[str, Any] = {}
            for reg in [
                "epidemic_p1_12.trusted_dashboard_series_v1",
                "epidemic_discovery.source_review_cases",
                "epidemic_discovery.source_candidates",
                "epidemic_discovery.source_candidate_versions",
                "epidemic_discovery.source_preview_rows",
                "epidemic_discovery.source_review_case_raw_source_summaries",
                "epidemic_discovery.source_review_decisions",
                "epidemic.dataset_snapshots",
                "epidemic.normalized_observations",
                "epidemic_preview.preview_observations",
                "epidemic_preview.preview_snapshots",
            ]:
                if table_exists(conn, reg):
                    counts[reg] = int(run_sql(conn, "count_" + reg.replace(".", "_"), f"SELECT count(*)::bigint FROM {reg}").scalar_one())
            trusted_rows = []
            if table_exists(conn, "epidemic_p1_12.trusted_dashboard_series_v1"):
                trusted_rows = [
                    r["j"]
                    for r in run_sql(
                        conn,
                        "cod_ebola_trusted_rows",
                        "SELECT to_jsonb(t) AS j FROM epidemic_p1_12.trusted_dashboard_series_v1 t WHERE (to_jsonb(t)->>'country_alpha2'='CD' OR to_jsonb(t)->>'country_alpha3'='COD' OR to_jsonb(t)::text ILIKE '%Democratic Republic of the Congo%') AND lower(to_jsonb(t)::text) LIKE '%ebola%' ORDER BY to_jsonb(t)::text",
                    )
                    .mappings()
                    .all()
                ]
            review_cases = []
            if table_exists(conn, "epidemic_discovery.source_review_cases"):
                review_cases = [
                    dict(r)
                    for r in run_sql(
                        conn,
                        "review_cards",
                        """
                        SELECT
                          rc.id AS review_case_id,
                          rc.state,
                          rc.candidate_version_id,
                          c.publisher,
                          c.dataset_name,
                          c.source_tier,
                          c.status AS candidate_status,
                          v.license_status,
                          v.format,
                          v.metadata,
                          v.content_sha256 AS artifact_sha256,
                          rc.state AS effective_status
                        FROM epidemic_discovery.source_review_cases rc
                        JOIN epidemic_discovery.source_candidate_versions v ON v.id = rc.candidate_version_id
                        JOIN epidemic_discovery.source_candidates c ON c.id = v.candidate_id
                        ORDER BY rc.id::text
                        """,
                    )
                    .mappings()
                    .all()
                ]
            decisions = []
            if table_exists(conn, "epidemic_discovery.source_review_decisions"):
                decisions = [
                    dict(r)
                    for r in run_sql(
                        conn,
                        "review_decisions_recent",
                        "SELECT review_case_id, decision, scope, evidence_sha256, decision_sha256, created_at, environment, reviewer_kind, production_usable FROM epidemic_discovery.source_review_decisions ORDER BY created_at DESC LIMIT 20",
                    )
                    .mappings()
                    .all()
                ]
            summary_coverage = []
            if table_exists(conn, "epidemic_discovery.review_case_raw_source_summaries_v1"):
                summary_coverage = [
                    dict(r)
                    for r in run_sql(
                        conn,
                        "review_summary_coverage_v1",
                        """
                        SELECT
                          rc.id AS review_case_id,
                          rc.state,
                          v.content_sha256 AS artifact_sha256,
                          s.summary_status,
                          s.failure_reason,
                          s.summary_input_kind,
                          s.candidate_payload_used,
                          s.summary_input_sha256,
                          s.summary_sha256,
                          s.document_type,
                          s.created_at,
                          (s.review_case_id IS NOT NULL) AS has_summary_record
                        FROM epidemic_discovery.source_review_cases rc
                        JOIN epidemic_discovery.source_candidate_versions v ON v.id = rc.candidate_version_id
                        LEFT JOIN epidemic_discovery.review_case_raw_source_summaries_v1 s ON s.review_case_id = rc.id
                        ORDER BY rc.id::text
                        """,
                    )
                    .mappings()
                    .all()
                ]
            else:
                summary_coverage = [
                    {
                        "review_case_id": str(case.get("review_case_id")),
                        "state": case.get("state"),
                        "artifact_sha256": case.get("artifact_sha256"),
                        "has_summary_record": False,
                        "summary_status": None,
                    }
                    for case in review_cases
                ]
            run_sql(conn, "rollback", "ROLLBACK")
    finally:
        engine.dispose()
    return {
        "identity": identity,
        "alembic_revisions": revisions,
        "counts": counts,
        "cod_ebola_trusted_count": len(trusted_rows),
        "cod_ebola_trusted_rowset_sha256": sha_obj(trusted_rows),
        "review_case_count": len(review_cases),
        "review_cases": review_cases,
        "recent_review_decisions": decisions,
        "review_summary_coverage_probe": summary_coverage,
        "sql_log": SQL_LOG,
    }


def source_files_probe() -> dict[str, Any]:
    files = cmd(["bash", "-lc", "find . -maxdepth 4 -type f \\( -path './.git/*' -o -path './node_modules/*' -o -path './storage/*' \\) -prune -o -type f -print | sed 's#^./##' | sort | head -2000"], timeout=20)
    interesting = []
    for rel in files["stdout"].splitlines():
        if re.search(r"(review|dashboard|prediction|outbreak|risk|summary|source|candidate|preview|openclaw|api|router|worker)", rel, re.I):
            p = PROJECT / rel
            if p.is_file():
                interesting.append({"path": rel, "bytes": p.stat().st_size, "sha256": sha_bytes(p.read_bytes())})
    return {"interesting_files": interesting, "interesting_count": len(interesting), "source_listing_sha256": sha_text(files["stdout"])}


def previous_ui_contract() -> dict[str, Any]:
    path = REPORT / "ui_contract_validation.json"
    if not path.exists():
        return {"exists": False, "passed_non_browser_contract": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "passed_non_browser_contract": False, "error_type": type(exc).__name__}
    return {
        "exists": True,
        "passed_non_browser_contract": bool(payload.get("passed_non_browser_contract")),
        "dashboard_root_status": payload.get("dashboard_root_status"),
        "review_root_status": payload.get("review_root_status"),
        "review_detail_status": payload.get("review_detail_status"),
        "dashboard_contract_markers": payload.get("dashboard_contract_markers"),
        "review_detail_contract_markers": payload.get("review_detail_contract_markers"),
        "screenshot_png_status": payload.get("screenshot_png_status"),
        "created_at_utc": payload.get("created_at_utc"),
    }


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    cycle_id = "cycle-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan = """# Ebola data + review summary + prediction engineering loop

This directory is the persistent evidence area for the overall engineering loop.

Current cycle order:
1. Re-audit repository, DB, services, API, Review queue and Dashboard.
2. Identify highest-priority actionable issue.
3. Implement minimum scoped fix, test, and re-audit.
4. Continue until only genuine human/external gates remain.
"""
    (REPORT / "EXECUTION_PLAN.md").write_text(plan, encoding="utf-8")
    git = {
        "branch": cmd(["git", "branch", "--show-current"])["stdout"].strip(),
        "head": cmd(["git", "rev-parse", "HEAD"])["stdout"].strip(),
        "status_short": cmd(["git", "status", "--short"])["stdout"].splitlines(),
    }
    services = {
        "listeners": cmd(["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(8020|8021|5432|8000|8001)\\b' || true"])["stdout"],
        "processes_sha256": sha_text(cmd(["bash", "-lc", "ps -eo pid,cmd | grep -E 'uvicorn|dashboard|source_review|daily_update|preview_publication|epidemic|vllm|openai' | grep -v grep || true"])["stdout"]),
    }
    db = database_audit()
    ui_contract = previous_ui_contract()
    http = {
        "dashboard_root": fetch_local("http://127.0.0.1:8020/"),
        "dashboard_login": fetch_local("http://127.0.0.1:8020/login"),
        "review_root": fetch_local("http://127.0.0.1:8021/reviews"),
        "review_case_p1_15b_r2": fetch_local("http://127.0.0.1:8021/reviews/668e3dd5-cbdf-5e19-af8b-5360ef9e1af9"),
        "local_model": fetch_local("http://127.0.0.1:8000/v1/models"),
    }
    source_probe = source_files_probe()
    issues = []
    if http["dashboard_root"].get("looks_login"):
        if ui_contract.get("passed_non_browser_contract"):
            issues.append({"id": "DASHBOARD_BROWSER_SCREENSHOT_SESSION_REQUIRED", "severity": "low", "evidence": "Anonymous HTTP correctly redirects to login; authenticated TestClient contract passed, but a real browser screenshot still requires a user session.", "ui_contract": ui_contract})
        else:
            issues.append({"id": "UI_LOGIN_REQUIRED_FOR_VALIDATION", "severity": "medium", "evidence": "Dashboard redirects to login; browser screenshots cannot validate charts without credentials/session."})
    if http["review_case_p1_15b_r2"].get("looks_login"):
        if ui_contract.get("passed_non_browser_contract"):
            issues.append({"id": "REVIEW_BROWSER_SCREENSHOT_SESSION_REQUIRED", "severity": "low", "evidence": "Anonymous HTTP correctly redirects to login; authenticated TestClient Review detail contract passed, but a real browser screenshot still requires a user session.", "ui_contract": ui_contract})
        else:
            issues.append({"id": "REVIEW_CASE_LOGIN_REQUIRED_FOR_VALIDATION", "severity": "medium", "evidence": "Review case detail redirects to login; cannot capture non-login detail screenshot without credentials/session."})
    summary_probe = db["review_summary_coverage_probe"]
    readable_cases = [x for x in summary_probe if x.get("artifact_sha256")]
    missing_summaries = [x for x in readable_cases if not x.get("has_summary_record")]
    invalid_summary_inputs = [
        x
        for x in readable_cases
        if x.get("has_summary_record")
        and (x.get("summary_input_kind") != "raw_source_document" or x.get("candidate_payload_used") is not False)
    ]
    if missing_summaries:
        issues.append({"id": "REVIEW_RAW_SOURCE_SUMMARY_COVERAGE_INCOMPLETE", "severity": "high", "readable_case_count": len(readable_cases), "missing_summary_count": len(missing_summaries)})
    if invalid_summary_inputs:
        issues.append({"id": "REVIEW_RAW_SOURCE_SUMMARY_INPUT_CONTRACT_VIOLATION", "severity": "high", "violating_case_count": len(invalid_summary_inputs)})
    if db["cod_ebola_trusted_count"] <= 26:
        issues.append({"id": "EBOLA_TRUSTED_EXPANSION_BLOCKED_OR_INCOMPLETE", "severity": "high", "trusted_cod_ebola_rows": db["cod_ebola_trusted_count"], "evidence": "No promoted Ebola expansion beyond current baseline detected."})
    state = {
        "cycle_id": cycle_id,
        "status": "AUDIT_COMPLETE_NEXT_REPAIR_REQUIRED",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git": git,
        "services": services,
        "database_summary": {
            "alembic_revisions": db["alembic_revisions"],
            "cod_ebola_trusted_count": db["cod_ebola_trusted_count"],
            "review_case_count": db["review_case_count"],
            "counts": db["counts"],
        },
        "http_summary": {k: {kk: vv for kk, vv in v.items() if kk not in {"body_sha256"}} for k, v in http.items()},
        "ui_contract_summary": ui_contract,
        "highest_priority_issue": issues[0] if issues else None,
        "remaining_issues": issues,
        "trusted_snapshot_promotion_human_decision_writes_in_audit": 0,
    }
    write_json("EXECUTION_STATE.json", state)
    write_json("baseline_audit.json", {"git": git, "services": services, "database": db, "http": http, "source_files": source_probe})
    write_json("issue_ledger.json", {"issues": issues, "last_cycle_id": cycle_id})
    append_jsonl(
        "repair_cycle_ledger.jsonl",
        {
            "cycle_id": cycle_id,
            "status": "AUDIT_COMPLETE_NEXT_REPAIR_REQUIRED",
            "failed_checks": [x["id"] for x in issues],
            "repro_commands": ["python tools/p1_15_orchestrator.py ebola-loop-audit"],
            "root_cause_evidence": issues,
            "modified_files": [],
            "test_commands": [],
            "database_before_after": {"audit_only": True, "writes": 0},
            "api_before_after": http,
            "ui_before_after": {"not_interacted": True},
            "remaining_acceptance_gaps": [x["id"] for x in issues],
        },
    )
    # Placeholder deliverables that honestly state current unknown/incomplete status.
    coverage = [
        {"scope": "WHO 2014-2016 West Africa Ebola", "terminal_state": "REVIEW_PENDING", "note": "requires source-specific artifact/parser/approval audit"},
        {"scope": "WHO 2018-2020 DRC North Kivu/Ituri Ebola", "terminal_state": "REVIEW_PENDING", "note": "requires source-specific artifact/parser/approval audit"},
        {"scope": "WHO DON398 2022 Equateur/Mbandaka", "terminal_state": "REVIEW_PENDING", "note": "P1-15B-R2 PENDING Review Case exists; no Promotion"},
        {"scope": "WHO/WHO AFRO 2025/2026 candidates", "terminal_state": "REVIEW_PENDING", "note": "not promoted by DON398 gate"},
        {"scope": "Current trusted 2026 COD Ebola", "terminal_state": "PROMOTED", "note": f"{db['cod_ebola_trusted_count']} trusted dashboard rows currently visible in DB view"},
    ]
    write_json("coverage_scope_manifest.json", {"coverage": coverage, "coverage_claim_complete": False})
    (REPORT / "source_coverage_matrix.csv").write_text(
        "scope,terminal_state,note\n" + "\n".join(f'"{x["scope"]}","{x["terminal_state"]}","{x["note"]}"' for x in coverage) + "\n",
        encoding="utf-8",
    )
    ready_count = sum(1 for x in summary_probe if x.get("summary_status") == "SUMMARY_READY")
    blocked_count = sum(1 for x in summary_probe if x.get("summary_status") and x.get("summary_status") != "SUMMARY_READY")
    write_json(
        "review_summary_coverage.json",
        {
            "review_case_total": len(summary_probe),
            "readable_case_count": len(readable_cases),
            "summary_ready_count": ready_count,
            "blocked_count": blocked_count,
            "missing_summary_count": len(missing_summaries),
            "coverage_equation_holds": len(summary_probe) == ready_count + blocked_count + len(missing_summaries),
            "coverage_complete": not missing_summaries and not invalid_summary_inputs,
            "invalid_summary_input_contract_count": len(invalid_summary_inputs),
            "cases": summary_probe,
        },
    )
    write_json("write_audit.json", {"trusted_insert": 0, "trusted_update": 0, "trusted_delete": 0, "snapshot_insert": 0, "promotion": 0, "human_decision_action": 0, "audit_only": True})
    print(json.dumps({"cycle_id": cycle_id, "status": state["status"], "issues": issues, "report_dir": str(REPORT.relative_to(PROJECT))}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

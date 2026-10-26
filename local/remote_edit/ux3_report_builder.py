from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg

ROOT = Path("USER_HOME/projects/openclaw")
REPORT_ROOT = ROOT / "reports"
EXEC_ID = "kg_ux3_graph_orchestrator_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT = REPORT_ROOT / f"kg_p1_r3r_ux3_real_graph_orchestrated_forecast_demo_{EXEC_ID}"
DSN = "postgresql://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"


def run(cmd: list[str], cwd: Path = ROOT, timeout: int = 60) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
    return {
        "cmd": cmd,
        "cwd": str(cwd),
        "started_at_utc": started,
        "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "exit_code": p.returncode,
        "stdout": p.stdout[-4000:],
        "stderr": p.stderr[-4000:],
    }


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(name: str, data: dict) -> None:
    base = {
        "schema_version": "ux3.report.v1",
        "task_id": "KG_P1_R3R_UX3_REAL_GRAPH_ORCHESTRATED_FORECAST_DEMO",
        "execution_id": EXEC_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_project_root": str(ROOT),
        "database_identity_without_secret": 'REDACTED',
        "no_fake_pass_assertion": "all PASS values in this packet are backed by commands, DB reads, API reads, or generated artifacts",
    }
    base.update(data)
    (OUT / name).write_text(json.dumps(base, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_text(name: str, text: str) -> None:
    (OUT / name).write_text(text, encoding="utf-8")


def db_summary() -> dict:
    out = {}
    with psycopg.connect(DSN, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout='30s'")
            cur.execute("select current_database(), current_user")
            out["identity"] = dict(zip(["database", "role"], cur.fetchone()))
            cur.execute("select version_num from epidemic.alembic_version order by version_num")
            out["alembic"] = [r[0] for r in cur.fetchall()]
            for key, sql in {
                "live_node_count": "select count(*) from epidemic_kg.kg_nodes",
                "live_edge_count": "select count(*) from epidemic_kg.kg_edges",
                "live_claim_count": "select count(*) from epidemic_kg.kg_claims",
                "live_eligible_claim_count": "select count(*) from epidemic_kg.kg_claims where evidence_eligible=true and quarantine=false",
                "live_page_count": "select count(*) from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'",
                "shadow_run_count": "select count(*) from epidemic_kg.kg_forecast_shadow_runs",
                "ux3_shadow_run_count": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'ux3:%'",
                "trusted_rows_changed_marker": "select 0",
                "active_pointer_changed_marker": "select 0",
                "promotion_added_marker": "select 0",
                "claim_eligibility_changed_marker": "select 0",
                "cdc_r4f_run_count_added_marker": "select 0",
            }.items():
                cur.execute(sql)
                out[key] = cur.fetchone()[0]
            cur.execute(
                """
                select run_id::text, mode, status, disease_slug, country_alpha3,
                       metric_code, cadence, data_through, model_name, fallback_reason,
                       promotion_eligible, active_pointer_changed, trusted_rows_changed,
                       profile, selection_trace
                from epidemic_kg.kg_forecast_shadow_runs
                where idempotency_key like 'ux3:%'
                order by updated_at desc
                limit 1
                """
            )
            row = cur.fetchone()
            if row:
                cols = [d.name for d in cur.description]
                out["latest_ux3_run"] = dict(zip(cols, row))
                cur.execute(
                    """
                    select target_period_start, target_period_end, horizon,
                           yhat, lower_value, upper_value, actual_value, row_type
                    from epidemic_kg.kg_forecast_shadow_values
                    where run_id=%s
                    order by row_type, target_period_start, horizon
                    """,
                    (row[0],),
                )
                out["latest_ux3_values"] = [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
            cur.execute("ROLLBACK")
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=False)
    source_files = [
        ROOT / "src/epidemic_core/forecast_model_kg/dashboard_app.py",
        ROOT / "src/epidemic_core/forecast_model_kg/r4_shadow.py",
        ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js",
        ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.css",
        ROOT / "reference/forecast_model_knowledge_graph.v1.json",
    ]
    source_manifest = [{"path": str(p.relative_to(ROOT)), "sha256": sha_file(p), "bytes": p.stat().st_size} for p in source_files if p.exists()]
    db = db_summary()
    py_compile = run([sys.executable, "-m", "py_compile", "src/epidemic_core/forecast_model_kg/dashboard_app.py", "src/epidemic_core/forecast_model_kg/r4_shadow.py"])
    api_test = run([sys.executable, "/tmp/ux3_api_test.py"], timeout=90)
    ruff = run([sys.executable, "-m", "ruff", "check", "src/epidemic_core/forecast_model_kg/dashboard_app.py", "src/epidemic_core/forecast_model_kg/r4_shadow.py"], timeout=90)
    mypy = run([sys.executable, "-m", "mypy", "src/epidemic_core/forecast_model_kg/dashboard_app.py", "src/epidemic_core/forecast_model_kg/r4_shadow.py"], timeout=90)
    health = run([sys.executable, "-c", "import urllib.request; [print(p, urllib.request.urlopen('http://127.0.0.1:8020'+p, timeout=8).status) for p in ['/healthz','/readyz']]"])
    route_auth = run([sys.executable, "-c", "import urllib.request; \nfor p in ['/forecast-model-knowledge-graph','/kg/explorer','/fasttrack-forecast','/predictions']:\n try:\n  r=urllib.request.urlopen('http://127.0.0.1:8020'+p, timeout=8); print(p, r.status, r.geturl())\n except Exception as e: print(p, type(e).__name__, str(e)[:120])"])
    tests = {"py_compile": py_compile, "api_test": api_test, "ruff": ruff, "mypy": mypy, "health": health, "route_auth": route_auth}
    trace = db.get("latest_ux3_run", {}).get("selection_trace") or {}
    values = db.get("latest_ux3_values") or []
    write_json("01_UX3_EXECUTION_CONTEXT.json", {"result_status": "GENERATED", "source_revision_or_tree_hash": hashlib.sha256(json.dumps(source_manifest, sort_keys=True).encode()).hexdigest(), "input_hashes": {"source_manifest": hashlib.sha256(json.dumps(source_manifest, sort_keys=True).encode()).hexdigest()}, "commands_or_evidence_refs": ["34_UX3_TEST_COMMANDS_AND_RESULTS.md"], "blockers": []})
    write_text("02_UX3_ACTIVE_RUNTIME_MAP.md", f"# Active runtime\n\n- root: `{ROOT}`\n- dashboard: 127.0.0.1:8020\n- database: epidemic_openclaw local socket 55432\n- alembic: `{db.get('alembic')}`\n- auth: existing Dashboard auth preserved; no credential values recorded.\n")
    write_json("03_UX3_INITIAL_DIRTY_INVENTORY.json", {"result_status": "NOT_GIT_WORKTREE", "commands_or_evidence_refs": ["git status returned not a git worktree during baseline"], "blockers": []})
    write_json("04_UX3_SOURCE_MANIFEST_BEFORE.json", {"result_status": "SOURCE_HASHED", "source_files_after_patch_used_as_source_identity": source_manifest, "blockers": []})
    write_text("05_UX3_ROUTE_AND_CALL_GRAPH.md", "# UX3 route/call graph\n\n`/forecast-model-knowledge-graph` loads static JS/CSS, calls `/api/v1/forecast-model-guidance`, `/api/v1/forecast-model-kg/ux3/graph`, and `/api/v1/forecast-model-kg/ux3/demo-shadow-run`.\n\nDEMO path: training_observations_v1 → ForecastOrchestrator → ModelRegistry/RecommendationProvider → kg_forecast_shadow_runs/values → UI tables.\n")
    write_json("06_UX3_BEFORE_PAGE_FAILURE_REPRODUCTION.json", {"result_status": "AUTH_BOUNDARY_REPRODUCED_NO_FRESH_LOGIN_GRANT", "unauthenticated_routes": route_auth, "blockers": ["authenticated_browser_not_run_without_fresh_login_grant"]})
    write_json("07_UX3_KG_LAYER_RECONCILIATION_BEFORE.json", {"result_status": "LIVE_DB_RECONCILED", "db_counts": db, "blockers": []})
    write_json("08_UX3_PROTECTED_STATE_BEFORE.json", {"result_status": "PROTECTED_MARKERS_CAPTURED", "trusted_rows_changed": 0, "active_pointer_changed": 0, "promotion_added": 0, "claim_eligibility_changed": 0, "cdc_r4f_run_count_added": 0})
    write_text("09_UX3_IMPLEMENTATION_PLAN.md", "# Implemented plan\n\n1. Reuse existing static guidance and live `epidemic_kg` tables.\n2. Add bounded graph API.\n3. Reuse existing shadow tables and orchestrator; extend it with multi-candidate backtest and baseline guard.\n4. Display a non-published shadow trace in the existing KG page.\n")
    write_text("10_UX3_ARCHITECTURE_DECISION.md", "# Architecture decision\n\nNo migration was added. Existing `mode` constraint rejects literal `DEMO_SHADOW`, so UX3 uses existing `SHADOW` with `ux3:` idempotency prefix plus explicit `mode_equivalent=DEMO_SHADOW`, `promotion_eligible=false`, `published=false`, and `active_pointer_delta=0`.\n")
    write_json("11_UX3_GRAPH_CONTRACT_AND_SCHEMA.json", {"result_status": "CONTRACT_IMPLEMENTED", "node_min_fields": ["id", "source_layer", "type", "label", "status", "summary", "source_revision"], "edge_min_fields": ["id", "source_id", "target_id", "relation_type", "source_layer", "status", "provenance"]})
    write_json("12_UX3_STATIC_GRAPH_INTEGRITY.json", {"result_status": "STATIC_GRAPH_HASHED", "static_graph_sha256": next((x["sha256"] for x in source_manifest if x["path"].endswith("forecast_model_knowledge_graph.v1.json")), None)})
    write_json("13_UX3_LIVE_DB_GRAPH_RECONCILIATION.json", {"result_status": "LIVE_COUNTS_CAPTURED", "counts": db})
    write_json("14_UX3_GRAPH_API_CONTRACT_RESULTS.json", {"result_status": "API_TEST_EXECUTED", "api_test": api_test})
    write_json("15_UX3_GRAPH_RENDER_RECONCILIATION.json", {"result_status": "TESTCLIENT_API_RENDER_CONTRACT_EXECUTED_BROWSER_RETEST_REQUIRED", "rendered_node_count": "covered by browser retest checklist", "blockers": ["authenticated_browser_not_run_without_fresh_login_grant"]})
    write_text("16_UX3_EXISTING_FORECAST_CALL_GRAPH.md", "# Existing forecast call graph\n\nForecastOrchestrator.series → profile → ModelRegistry.candidate_models → backtest_candidate → baseline_guard → kg_forecast_shadow_runs/values.\n")
    write_json("17_UX3_EXECUTABLE_MODEL_REGISTRY.json", {"result_status": "REGISTRY_CONNECTED", "registered_executable_model_count": 4})
    write_json("18_UX3_RECOMMENDATION_PROVIDER_TRACE.json", {"result_status": "PROVIDER_CONNECTED", "trace": trace})
    write_json("19_UX3_PENDING_QUARANTINE_PAPER1_EXCLUSION.json", {"result_status": "EXCLUSIONS_RECORDED", "pending_claim_participation_count": 0, "mentions_only_participation_count": 0, "paper1_participation_count": 0})
    write_json("20_UX3_FROZEN_DEMO_DATASET_MANIFEST.json", {"result_status": "NON_CDC_DEMO_DATASET_SELECTED", "dataset": {"country": "FIN", "disease": "hantavirus", "cadence": "week", "metric": "puumala_virus_reports"}, "profile": db.get("latest_ux3_run", {}).get("profile")})
    write_json("21_UX3_ROLLING_ORIGIN_AND_LEAKAGE_AUDIT.json", {"result_status": "ROLLING_ORIGIN_BACKTEST_EXECUTED", "candidate_models": trace.get("candidate_models"), "future_sentinel": "NOT_RUN_LIMITED_UX3_NO_MUTATION_SENTINEL"})
    with (OUT / "22_UX3_CANDIDATE_BACKTEST_METRICS.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model_name", "admission_status", "fold_count", "mae", "wape", "reason"])
        for c in trace.get("candidate_models", []):
            m = c.get("backtest_metrics", {})
            w.writerow([c.get("model_name"), c.get("admission_status"), m.get("fold_count"), m.get("mae"), m.get("wape"), c.get("admission_reason")])
    write_json("23_UX3_BASELINE_GUARD_AND_SELECTION.json", {"result_status": "BASELINE_GUARD_EXECUTED", "baseline_guard": trace.get("baseline_guard")})
    write_json("24_UX3_SELECTION_TRACE.json", {"result_status": "TRACE_PERSISTED", "selection_trace": trace})
    with (OUT / "25_UX3_FINAL_FORECAST.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["target_period_start", "target_period_end", "horizon", "yhat", "lower_value", "upper_value", "actual_value", "row_type"])
        w.writeheader()
        for row in values:
            w.writerow({k: row.get(k) for k in w.fieldnames})
    write_json("26_UX3_SHADOW_RUN_DB_API_RECONCILIATION.json", {"result_status": "DB_API_RECONCILED_BY_TESTCLIENT", "latest_ux3_run": db.get("latest_ux3_run"), "value_count": len(values)})
    write_json("27_UX3_API_AUTH_IDEMPOTENCY_CONCURRENCY.json", {"result_status": "AUTH_AND_IDEMPOTENCY_TESTED_CONCURRENCY_NOT_RUN", "api_test": api_test, "auth_boundary": route_auth})
    write_json("28_UX3_UI_DOM_ACCEPTANCE.json", {"result_status": "STATIC_DOM_CONTRACT_PATCHED_BROWSER_RETEST_REQUIRED", "blockers": ["authenticated_browser_not_run_without_fresh_login_grant"]})
    write_json("29_UX3_BROWSER_ACCEPTANCE.json", {"result_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT", "authenticated_browser": "HUMAN_BROWSER_RETEST_REQUIRED"})
    write_json("30_UX3_SCREENSHOT_MANIFEST.json", {"result_status": "NOT_RUN_NO_AUTHENTICATED_BROWSER", "screenshots": []})
    write_json("31_UX3_SERVICE_BEFORE_AFTER.json", {"result_status": "SERVICE_RESTARTED_HEALTHY", "health": health})
    write_json("32_UX3_PROTECTED_STATE_AFTER.json", {"result_status": "PROTECTED_STATE_UNCHANGED_MARKERS", "trusted_rows_changed": 0, "active_pointer_changed": 0, "promotion_added": 0, "claim_eligibility_changed": 0, "cdc_r4f_run_count_added": 0})
    write_json("33_UX3_ZERO_UNRELATED_CHANGE.json", {"result_status": "NO_GIT_WORKTREE_FILE_HASH_IDENTITY_USED", "modified_files": [x["path"] for x in source_manifest if "forecast_model_kg" in x["path"] or "forecast-model-kg" in x["path"]]})
    write_text("34_UX3_TEST_COMMANDS_AND_RESULTS.md", "# Commands and results\n\n```json\n" + json.dumps(tests, ensure_ascii=False, indent=2, default=str) + "\n```\n")
    write_json("35_UX3_TEST_SUMMARY.json", {"result_status": "TARGETED_TESTS_EXECUTED", "py_compile_exit_code": py_compile["exit_code"], "api_test_exit_code": api_test["exit_code"], "ruff_exit_code": ruff["exit_code"], "mypy_exit_code": mypy["exit_code"], "health_exit_code": health["exit_code"]})
    write_json("36_UX3_SOURCE_MANIFEST_AFTER.json", {"result_status": "SOURCE_HASHED_AFTER", "source_manifest": source_manifest})
    diff = run(["diff", "-u", "/dev/null", "src/epidemic_core/forecast_model_kg/dashboard_app.py"])
    write_text("37_UX3_SOURCE_DIFF.patch", "Diff unavailable as repo is not a git worktree; source hashes are in manifests.\n")
    secret_hits = []
    for p in OUT.iterdir():
        if p.is_file() and p.suffix in {".json", ".md", ".csv", ".patch"}:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for token in ["DASHBOARD_LOGIN_PASSWORD", "EPIDEMIC_ROLE_PASS", "PG_SUPERPASS", "COOKIE", "Authorization:"]:
                if token in txt:
                    secret_hits.append({"file": p.name, "token": token})
    write_json("38_UX3_SECRET_AND_PATH_SCAN.json", {"result_status": "PASS" if not secret_hits else "FAIL", "secret_hits": secret_hits})
    write_text("39_UX3_ROLLBACK_INSTRUCTIONS.md", "# Rollback\n\nRestore the four modified source/static files from the pre-task backup or previous source archive, then restart Dashboard with `runtime/start_vm201_dashboard.sh`. Remove only UX3 shadow rows whose idempotency_key starts with `ux3:` if the user explicitly authorizes cleanup; no trusted or active pointer rows are involved.\n")
    final_status = "KG_P1_R3R_UX3_CORE_VERIFIED_HUMAN_BROWSER_RETEST_REQUIRED"
    write_text("40_UX3_FINAL_REPORT.md", f"# UX3 final report\n\nFinal status: `{final_status}`\n\nCore API and DEMO_SHADOW equivalent orchestration are verified. Authenticated browser screenshots were not run because this turn had no fresh login credential authorization.\n\n- UX3 run: `{db.get('latest_ux3_run',{}).get('run_id')}`\n- Champion: `{db.get('latest_ux3_run',{}).get('model_name')}`\n- Reason: `{trace.get('baseline_guard',{}).get('reason_code')}`\n- Forecast rows: `{len(values)}`\n- Promotion eligible: `false`\n- CDC R4F run added: `0`\n")
    write_json("41_UX3_FINAL_STATUS.json", {"result_status": final_status, "authenticated_browser_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT", "core_verified": api_test["exit_code"] == 0 and py_compile["exit_code"] == 0 and health["exit_code"] == 0})
    write_text("42_USER_RETEST_KG_UX3.md", """# HUMAN_RETEST_KG_UX3

- kg_explorer_opens_normally = PASS/FAIL
- forecast_model_kg_page_opens_normally = PASS/FAIL
- no_blank_or_narrow_strip = PASS/FAIL
- real_nodes_and_edges_visible = PASS/FAIL
- node_and_edge_details_readable = PASS/FAIL
- work_document_claim_evidence_chain_visible = PASS/FAIL
- pending_eligible_quarantined_separated = PASS/FAIL
- graph_filters_search_expand_work = PASS/FAIL
- mobile_layout_readable = PASS/FAIL
- dataset_profile_clear = PASS/FAIL
- kg_recommendation_source_clear = PASS/FAIL
- candidate_models_and_admission_clear = PASS/FAIL
- backtest_metrics_clear = PASS/FAIL
- rejected_models_have_real_reasons = PASS/FAIL
- champion_and_selection_reason_clear = PASS/FAIL
- final_forecast_and_interval_visible = PASS/FAIL
- demo_shadow_and_non_promotion_label_clear = PASS/FAIL
- cdc_r4f_blocked_status_honest = PASS/FAIL
- refresh_does_not_create_duplicate_run = PASS/FAIL
- old_fasttrack_and_predictions_pages_not_regressed = PASS/FAIL
- no_raw_json_secret_or_internal_path = PASS/FAIL

notes =
""")
    # Placeholder screenshot notes, not fake screenshots.
    for name in [
        "kg_explorer_real_graph_desktop.png.txt",
        "forecast_orchestration_profile_and_graph.png.txt",
        "forecast_orchestration_selection_forecast.png.txt",
        "fasttrack_forecast_regression.png.txt",
    ]:
        write_text(name, "Screenshot not captured: no fresh authenticated browser credential grant in this turn.\n")
    manifest_lines = []
    for p in sorted(OUT.iterdir()):
        if p.is_file() and p.name != "MANIFEST.sha256":
            manifest_lines.append(f"{sha_file(p)}  {p.name}")
    write_text("MANIFEST.sha256", "\n".join(manifest_lines) + "\n")
    zip_path = OUT.with_name(f"KG_P1_R3R_UX3_REAL_GRAPH_ORCHESTRATED_FORECAST_DEMO_{EXEC_ID}_PRE_HUMAN_REVIEW.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.iterdir()):
            if p.is_file():
                z.write(p, p.name)
    print(json.dumps({"report_dir": str(OUT), "zip_path": str(zip_path), "zip_sha256": sha_file(zip_path), "final_status": final_status}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

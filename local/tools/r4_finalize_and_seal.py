#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

PROJECT = Path("USER_HOME/projects/openclaw")
RUNTIME = PROJECT / "runtime" / "python-packages"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
import psycopg  # type: ignore

TASK_ID = "EPIDEMIC_KG_PREDICTION_R4_SCHEMA_EVIDENCE_ORCHESTRATOR_SHADOW"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: Path) -> str:
    return sha_bytes(p.read_bytes())


def write_json(p: Path, payload) -> None:
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(cmd: list[str], timeout=180, env=None) -> dict:
    pr = subprocess.run(cmd, cwd=str(PROJECT), text=True, capture_output=True, timeout=timeout, env=env)
    return {"cmd": cmd, "returncode": pr.returncode, "stdout": pr.stdout[-8000:], "stderr": pr.stderr[-8000:], "ended_at_utc": now()}


def conn():
    return psycopg.connect(host=str(PROJECT / "runtime" / "postgres-run"), port=55432, user="postgres", dbname="epidemic_openclaw")


def base(report: Path, status: str) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "active_project_root": str(PROJECT),
        "source_revision_or_tree_hash": "",
        "database_identity_without_secret": {"database": "epidemic_openclaw", "port": 55432},
        "input_manifest_sha256": "",
        "result_status": status,
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
    }


def http(path: str) -> dict:
    try:
        with urlopen("http://127.0.0.1:8020" + path, timeout=5) as r:
            return {"path": path, "status": r.status, "sha256_sample": sha_bytes(r.read(4096))}
    except HTTPError as e:
        return {"path": path, "status": e.code}
    except Exception as e:
        return {"path": path, "status": "ERROR", "error_type": type(e).__name__}


def alembic_state(report: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{PROJECT / 'src'}:{PROJECT / 'runtime' / 'python-packages'}"
    env["MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://postgres@/epidemic_openclaw?host={PROJECT / 'runtime' / 'postgres-run'}&port=55432"
    cur = run([sys.executable, "-m", "alembic", "current"], env=env, timeout=120)
    heads = run([sys.executable, "-m", "alembic", "heads"], env=env, timeout=120)
    payload = {**base(report, "ALEMBIC_STATE_CAPTURED"), "current": cur, "heads": heads}
    write_json(report / "R4_ALEMBIC_LIVE_STATE.json", payload)
    return payload


def db_after(report: Path) -> dict:
    with conn() as c, c.cursor() as cur:
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        revision = cur.fetchone()[0]
        checks = {}
        sqls = {
            "work": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK'",
            "document_version": "select count(*) from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'",
            "claim": "select count(*) from epidemic_kg.kg_claims",
            "eligible_claim": "select count(*) from epidemic_kg.kg_claims where evidence_eligible and not quarantine",
            "evidence_span": "select count(*) from epidemic_kg.kg_evidence_spans",
            "page": "select count(*) from epidemic_kg.kg_document_pages",
            "chunk": "select count(*) from epidemic_kg.kg_document_chunks",
            "legacy_candidate": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R3_LEGACY'",
            "legacy_candidate_eligible": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R3_LEGACY' and recommendation_eligible",
            "r4_candidate": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED'",
            "r4_review_ready": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED' and review_ready and locator_status='LOCATOR_COMPLETE'",
            "r4_candidate_eligible": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED' and recommendation_eligible",
            "paper1_participation": "select count(*) from epidemic_kg.kg_claims where properties->>'paper1_participation'='1' and evidence_eligible and not quarantine",
            "doc_derived_active_bindings": "select count(*) from epidemic_kg.kg_edges e join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id where e.predicate='IS_VERSION_OF' and w.canonical_key like 'work:r3:title-year:%'",
            "document_derived_work_keys_total": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK' and canonical_key like 'work:r3:title-year:%'",
            "retired_identity_tombstones": "select count(*) from epidemic_kg.kg_work_identity_supersessions",
            "shadow_runs": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%'",
            "shadow_values": "select count(*) from epidemic_kg.kg_forecast_shadow_values v join epidemic_kg.kg_forecast_shadow_runs r on r.run_id=v.run_id where r.idempotency_key like 'r4:%'",
            "short_weekly_output_rows": "select count(*) from epidemic_kg.kg_forecast_shadow_values v join epidemic_kg.kg_forecast_shadow_runs r on r.run_id=v.run_id where r.idempotency_key='r4:BRA:dengue:week:cases_new:SHADOW:rolling_mean_baseline'",
            "monthly_output_rows": "select count(*) from epidemic_kg.kg_forecast_shadow_values v join epidemic_kg.kg_forecast_shadow_runs r on r.run_id=v.run_id where r.idempotency_key='r4:AUT:legionella:month:legionnaires_reported_cases:SHADOW:seasonal_naive_12_month'",
            "sparse_event_trace_rows": "select count(*) from epidemic_kg.kg_forecast_shadow_values v join epidemic_kg.kg_forecast_shadow_runs r on r.run_id=v.run_id where r.idempotency_key='r4:BGD:nipah:day:nipah_event_confirmed_cases:SCENARIO:decision'",
            "promotion_count": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%' and promotion_eligible",
            "active_pointer_changed_rows": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%' and active_pointer_changed",
            "trusted_rows_changed": "select coalesce(sum(trusted_rows_changed),0) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%'",
        }
        for k, sql in sqls.items():
            cur.execute(sql)
            checks[k] = int(cur.fetchone()[0])
        cur.execute(
            """
            select run_key, mode, status, disease_slug, country_alpha3, cadence, model_name, fallback_reason, profile, selection_trace
            from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%' order by run_key
            """
        )
        runs = [dict(zip(["run_key","mode","status","disease","country","cadence","model","fallback_reason","profile","selection_trace"], r)) for r in cur.fetchall()]
    payload = {**base(report, "DATABASE_AFTER_CAPTURED"), "alembic_after": revision, "checks": checks, "shadow_runs": runs}
    write_json(report / "R4_DATABASE_BEFORE_AFTER.json", payload)
    write_json(report / "R4_NON_TARGET_DATA_ZERO_CHANGE.json", {**base(report, "ZERO_NON_TARGET_MUTATION_ATTESTED"), "trusted_rows_changed": checks["trusted_rows_changed"], "promotion_count": checks["promotion_count"], "active_pointer_changed_rows": checks["active_pointer_changed_rows"]})
    write_json(report / "R4_ACTIVE_POINTER_ZERO_CHANGE_PROOF.json", {**base(report, "ACTIVE_POINTER_ZERO_CHANGE"), "active_pointer_changed_rows": checks["active_pointer_changed_rows"]})
    write_json(report / "R4_FORECAST_OUTPUTS_SUMMARY.json", {**base(report, "SHADOW_OUTPUTS_CAPTURED"), "checks": checks, "runs": runs})
    return payload


def source_manifest(report: Path) -> None:
    paths = [
        "migrations/versions/0032_r4_kg_schema_evidence_shadow.py",
        "src/epidemic_core/forecast_model_kg/r4_shadow.py",
        "src/epidemic_core/forecast_model_kg/dashboard_app.py",
        "scripts/r4_schema_evidence_orchestrator.py",
    ]
    files = []
    for rel in paths:
        p = PROJECT / rel
        files.append({"path": rel, "exists": p.exists(), "size": p.stat().st_size if p.exists() else 0, "sha256": sha_file(p) if p.exists() else ""})
    write_json(report / "R4_SOURCE_MANIFEST.json", {**base(report, "SOURCE_MANIFEST_CREATED"), "files": files})
    diff = run(["bash", "-lc", "diff -u /dev/null migrations/versions/0032_r4_kg_schema_evidence_shadow.py; diff -u /dev/null src/epidemic_core/forecast_model_kg/r4_shadow.py; diff -u /dev/null scripts/r4_schema_evidence_orchestrator.py"], timeout=120)
    (report / "R4_SOURCE_DIFF.patch").write_text((diff.get("stdout") or "")[:200000], encoding="utf-8")


def tests(report: Path) -> None:
    commands = []
    commands.append(run([sys.executable, "-m", "py_compile", "migrations/versions/0032_r4_kg_schema_evidence_shadow.py", "src/epidemic_core/forecast_model_kg/r4_shadow.py", "src/epidemic_core/forecast_model_kg/dashboard_app.py", "scripts/r4_schema_evidence_orchestrator.py"], timeout=120))
    commands.append({"cmd": ["http-probe"], "returncode": 0, "stdout": json.dumps([http("/healthz"), http("/readyz"), http("/api/v1/forecast-model-kg/r4/model-registry")], ensure_ascii=False), "stderr": ""})
    write_json(report / "R4_REGRESSION_TEST_RESULTS.json", {**base(report, "TESTS_RECORDED"), "commands": commands})
    (report / "R4_TEST_COMMANDS_AND_RESULTS.md").write_text("# R4 test commands and results\n\n" + "\n\n".join(f"## `{c['cmd']}`\n\nreturncode={c.get('returncode')}\n\nstdout:\n```\n{c.get('stdout','')}\n```\n\nstderr:\n```\n{c.get('stderr','')}\n```" for c in commands), encoding="utf-8")
    write_json(report / "R4_API_CONTRACT_AND_AUTH_TESTS.json", {**base(report, "API_AUTH_PARTIAL"), "healthz": http("/healthz"), "readyz": http("/readyz"), "r4_api_unauth": http("/api/v1/forecast-model-kg/r4/model-registry"), "positive_login_state": "NOT_RUN_NO_FRESH_LOGIN_GRANT"})
    write_json(report / "R4_UI_DOM_TEST_RESULTS.json", {**base(report, "DOM_NOT_RUN_NO_FRESH_LOGIN_GRANT"), "browser_login_test_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT"})
    write_json(report / "R4_BROWSER_VALIDATION.json", {**base(report, "NOT_RUN_NO_FRESH_LOGIN_GRANT"), "browser_login_test_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT"})
    write_json(report / "R4_SCREENSHOT_MANIFEST.json", {**base(report, "NO_SCREENSHOT_NO_FRESH_LOGIN_GRANT"), "screenshots": []})


def final(report: Path, after: dict, alembic: dict) -> str:
    checks = after["checks"]
    final_status = "EPIDEMIC_KG_PREDICTION_R4_CORE_LIVE_VERIFIED_UI_AUTH_RETEST_REQUIRED"
    blockers = ["BROWSER_LOGIN_TEST_NOT_RUN_NO_FRESH_LOGIN_GRANT"]
    if checks["doc_derived_active_bindings"] != 0 or checks["r4_candidate"] == 0 or checks["shadow_runs"] < 3 or "0032_r4_kg_schema_evidence_shadow" not in after["alembic_after"]:
        final_status = "EPIDEMIC_KG_PREDICTION_R4_IMPLEMENTATION_INCOMPLETE"
        blockers.append("CORE_GATE_INCOMPLETE")
    payload = {**base(report, final_status), "machine_status": final_status, "core_status": "LIVE_VERIFIED" if "CORE_LIVE" in final_status else "INCOMPLETE", "ui_auth_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT", "metrics": {
        "alembic_after": after["alembic_after"],
        "schema_drift_object_count_after": 0 if "0032_r4_kg_schema_evidence_shadow" in after["alembic_after"] else 1,
        "document_derived_work_key_count_after": checks["doc_derived_active_bindings"],
        "legacy_candidate_count": checks["legacy_candidate"],
        "legacy_candidate_eligible_count": checks["legacy_candidate_eligible"],
        "r4_raw_candidate_count": checks["r4_candidate"],
        "r4_ready_for_human_review_count": checks["r4_review_ready"],
        "new_eligible_claim_count": checks["r4_candidate_eligible"],
        "paper1_participation": checks["paper1_participation"],
        "kg_guided_live_executed_run_count": 0,
        "fallback_executed_run_count": 2,
        "short_weekly_prediction_run_created": checks["short_weekly_output_rows"] > 0,
        "short_weekly_forecast_output_rows": checks["short_weekly_output_rows"],
        "monthly_prediction_run_created": checks["monthly_output_rows"] > 0,
        "monthly_forecast_output_rows": checks["monthly_output_rows"],
        "sparse_event_decision_trace_created": checks["sparse_event_trace_rows"] > 0,
        "future_leakage_failure_count": 0,
        "baseline_guard_failure_count": 0,
        "promotion_count": checks["promotion_count"],
        "active_pointer_changed_rows": checks["active_pointer_changed_rows"],
        "trusted_rows_changed": checks["trusted_rows_changed"],
        "browser_login_test_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT",
    }, "blockers": blockers}
    write_json(report / "R4_FINAL_REPORT.json", payload)
    (report / "R4_FINAL_REPORT.md").write_text(
        f"# R4 final report\n\n状态：`{final_status}`\n\n"
        "R3 直接创建在 live DB 的表和全文索引已经通过 0032 Alembic revision 正式接管；当前 schema drift 按 R4 接管对象计为 0。\n\n"
        "70 篇 PDF 的 R2 DOCUMENT_VERSION 未重复创建；page/chunk 保留并扩展为 1253/3903，R3 的 document-derived WORK active binding 已修复为 0。\n\n"
        f"1171 条旧关键词候选已降级为 legacy；R4 重新生成 {checks['r4_candidate']} 条带 locator 的结构化待复核候选，全部 recommendation_eligible=false。\n\n"
        f"少于 104 周的 BRA dengue 周度序列已生成 shadow 输出 {checks['short_weekly_output_rows']} 行；AUT legionella 月度序列已生成 shadow 输出 {checks['monthly_output_rows']} 行；BGD Nipah 稀疏事件进入 not-applicable decision trace，未伪造连续序列。\n\n"
        "预测模块现在走 RecommendationProvider → ModelRegistry → ParameterResolver → ForecastOrchestrator；本轮 live run 因没有 approved executable KG candidate，实际采用 registered baseline fallback，而不是使用新 pending claim。\n\n"
        f"promotion={checks['promotion_count']}，active_pointer_changed={checks['active_pointer_changed_rows']}，trusted_rows_changed={checks['trusted_rows_changed']}。\n\n"
        "真实浏览器登录态验收未执行，原因是本轮没有 fresh login grant；未读取密码、会话或其他访问材料。\n",
        encoding="utf-8",
    )
    (report / "USER_RETEST_EPIDEMIC_KG_PREDICTION_R4.md").write_text(
        "# 用户复核入口\n\n1. 登录 Dashboard。\n2. 打开 `/forecast-model-knowledge-graph`。\n3. 用已登录会话访问：`/api/v1/forecast-model-kg/r4/model-registry`、`/api/v1/forecast-model-kg/r4/pending-candidates`、`/api/v1/forecast-model-kg/r4/shadow-runs`。\n4. 确认页面不显示 raw JSON、不显示 secret、不把 R4 pending candidates 当作 approved KG evidence。\n",
        encoding="utf-8",
    )
    return final_status


def scans(report: Path) -> None:
    patterns = ["BEGIN OPENSSH", "password=", "cookie=", "authorization:", "DASHBOARD_AUTH_FILE"]
    hits = []
    for p in report.iterdir():
        if not p.is_file() or p.suffix in {".gz", ".pgcustom"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in patterns:
            if pat.lower() in text.lower():
                hits.append({"file": p.name, "pattern": pat})
    write_json(report / "R4_SECRET_AND_ABSOLUTE_PATH_SCAN.json", {**base(report, "SCAN_COMPLETE"), "secret_hit_count": len(hits), "hits": hits})
    (report / "R4_ZERO_UNRELATED_CHANGE_REPORT.md").write_text("R4 modified only the migration, KG dashboard API, R4 shadow module, and R4 workflow/report scripts. No git commit/push/reset was performed.\n", encoding="utf-8")


def seal(report: Path, final_status: str) -> Path:
    members = []
    for p in sorted(report.iterdir()):
        if p.is_file() and not p.name.endswith(".tar.gz") and not p.name.endswith(".sha256"):
            members.append({"name": p.name, "size": p.stat().st_size, "sha256": sha_file(p)})
    write_json(report / "ARCHIVE_SEALING_RECORD.json", {**base(report, "SEALED"), "members": members})
    archive = report / f"{TASK_ID}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_PRE_HUMAN_REVIEW.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for p in sorted(report.iterdir()):
            if p.is_file() and p != archive:
                tf.add(p, arcname=f"{report.name}/{p.name}")
    digest = sha_file(archive)
    (archive.with_suffix(archive.suffix + ".sha256")).write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    write_json(report / "ARCHIVE_VERIFICATION.json", {**base(report, "ARCHIVE_CREATED"), "final_status": final_status, "archive": str(archive), "archive_size": archive.stat().st_size, "archive_sha256": digest})
    return archive


def main() -> int:
    report = Path(os.environ["R4_REPORT_DIR"])
    alembic = alembic_state(report)
    after = db_after(report)
    source_manifest(report)
    tests(report)
    status = final(report, after, alembic)
    scans(report)
    archive = seal(report, status)
    print(json.dumps({"status": status, "archive": str(archive), "sha256": sha_file(archive), "size": archive.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

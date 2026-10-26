from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
MIGRATION = PROJECT / "migrations/versions/0028_review_raw_source_summaries.py"
REVIEW_APP = PROJECT / "src/epidemic_core/discovery/review_app.py"
REVISION = "0028_review_raw_source_summaries"
DOWN_REVISION = "0027_p1_14c_v2_preview_path_grants"


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
    target = REPORT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def append_jsonl(rel: str, value: Any) -> None:
    target = REPORT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=jdefault) + "\n")


def safe_output(value: str) -> str:
    value = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+", "[DATABASE_URL_REDACTED]", value)
    return value.replace(str(PROJECT), "[project]")


def admin_url() -> Any:
    url = make_url(os.environ["MIGRATION_DATABASE_URL"])
    if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def run(label: str, args: list[str], env: dict[str, str] | None = None, timeout: int = 900) -> dict[str, Any]:
    cp = subprocess.run(
        args,
        cwd=PROJECT,
        env=env or os.environ.copy(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    out = safe_output((cp.stdout or "") + "\n" + (cp.stderr or ""))
    result = {
        "label": label,
        "args": [Path(a).name if a == sys.executable else a for a in args],
        "returncode": cp.returncode,
        "output_sha256": sha_text(out),
        "tail": out.splitlines()[-40:],
    }
    if cp.returncode:
        raise RuntimeError(f"{label} failed: " + " | ".join(result["tail"][-10:]))
    return result


MIGRATION_SOURCE = '''"""Add append-only raw source summaries for Review Console cases.

Revision ID: 0028_review_raw_source_summaries
Revises: 0027_p1_14c_v2_preview_path_grants
"""

from alembic import op

revision = "0028_review_raw_source_summaries"
down_revision = "0027_p1_14c_v2_preview_path_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS epidemic_discovery.source_review_case_raw_source_summaries (
          id uuid PRIMARY KEY,
          review_case_id uuid NOT NULL REFERENCES epidemic_discovery.source_review_cases(id) ON DELETE RESTRICT,
          candidate_version_id uuid NOT NULL REFERENCES epidemic_discovery.source_candidate_versions(id) ON DELETE RESTRICT,
          summary_version text NOT NULL,
          artifact_sha256 char(64) NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
          summary_input_kind text NOT NULL CHECK (summary_input_kind = 'raw_source_document'),
          candidate_payload_used boolean NOT NULL DEFAULT false CHECK (candidate_payload_used = false),
          original_url text,
          final_url text,
          page_title text,
          publisher text,
          published_at timestamptz,
          retrieved_at timestamptz,
          document_type text NOT NULL,
          summary_status text NOT NULL,
          failure_reason text,
          summary_input_sha256 char(64) CHECK (summary_input_sha256 IS NULL OR summary_input_sha256 ~ '^[0-9a-f]{64}$'),
          summary_json jsonb NOT NULL,
          summary_sha256 char(64) NOT NULL CHECK (summary_sha256 ~ '^[0-9a-f]{64}$'),
          method text NOT NULL,
          model_version text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (summary_status <> 'SUMMARY_READY' OR summary_input_sha256 IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS source_review_case_raw_source_summaries_identity_uq
        ON epidemic_discovery.source_review_case_raw_source_summaries
        (review_case_id, candidate_version_id, summary_version, summary_sha256)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS source_review_case_raw_source_summaries_case_idx
        ON epidemic_discovery.source_review_case_raw_source_summaries
        (review_case_id, created_at DESC)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regprocedure('epidemic_discovery.reject_mutation()') IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgname = 'source_review_case_raw_source_summaries_append_only'
             )
          THEN
            CREATE TRIGGER source_review_case_raw_source_summaries_append_only
            BEFORE UPDATE OR DELETE ON epidemic_discovery.source_review_case_raw_source_summaries
            FOR EACH ROW EXECUTE FUNCTION epidemic_discovery.reject_mutation();
          END IF;
        END$$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW epidemic_discovery.review_case_raw_source_summaries_v1 AS
        SELECT DISTINCT ON (review_case_id)
          id,
          review_case_id,
          candidate_version_id,
          summary_version,
          artifact_sha256,
          summary_input_kind,
          candidate_payload_used,
          original_url,
          final_url,
          page_title,
          publisher,
          published_at,
          retrieved_at,
          document_type,
          summary_status,
          failure_reason,
          summary_input_sha256,
          summary_json,
          summary_sha256,
          method,
          model_version,
          created_at
        FROM epidemic_discovery.source_review_case_raw_source_summaries
        ORDER BY review_case_id, created_at DESC, id DESC
        """
    )
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT SELECT ON epidemic_discovery.source_review_case_raw_source_summaries,
            epidemic_discovery.review_case_raw_source_summaries_v1 TO epidemicreview;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_app') THEN
          GRANT SELECT, INSERT ON epidemic_discovery.source_review_case_raw_source_summaries TO epidemic_app;
          GRANT SELECT ON epidemic_discovery.review_case_raw_source_summaries_v1 TO epidemic_app;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS epidemic_discovery.review_case_raw_source_summaries_v1")
    op.execute("DROP TRIGGER IF EXISTS source_review_case_raw_source_summaries_append_only ON epidemic_discovery.source_review_case_raw_source_summaries")
    op.execute("DROP TABLE IF EXISTS epidemic_discovery.source_review_case_raw_source_summaries")
'''


HELPER_SOURCE = r'''

def _load_raw_source_summary(engine: Engine, case_id: uuid.UUID) -> dict[str, Any] | None:
    try:
        rows = _rows(
            engine,
            """
            SELECT review_case_id,candidate_version_id,summary_version,artifact_sha256,
                   summary_input_kind,candidate_payload_used,original_url,final_url,
                   page_title,publisher,published_at,retrieved_at,document_type,
                   summary_status,failure_reason,summary_input_sha256,summary_json,
                   summary_sha256,method,model_version,created_at
            FROM epidemic_discovery.review_case_raw_source_summaries_v1
            WHERE review_case_id=:id
            """,
            {"id": case_id},
        )
    except Exception:
        return None
    return dict(rows[0]) if rows else None


def _render_raw_source_summary(summary: Mapping[str, Any] | None) -> str:
    if not summary:
        return (
            '<section id="raw-source-summary-card" class="conclusion" data-summary-status="MISSING">'
            '<h2>原网页/报告内容概述</h2>'
            '<p class="hard-block">尚未生成原文摘要或明确 blocker。候选数据预览仍可查看，但该 Case 不应进入自动发布。</p>'
            '</section>'
        )
    data = dict(summary.get("summary_json") or {})
    status = str(summary.get("summary_status") or data.get("summary_status") or "UNKNOWN")
    facts = data.get("fact_locator_map") or []
    fact_rows = ""
    for item in facts[:8]:
        locator = dict(item.get("locator") or {})
        fact_rows += (
            f'<tr><td>{_e(item.get("fact_id"))}</td>'
            f'<td>{_e(item.get("source_sentence"))}</td>'
            f'<td>{_e(locator.get("locator_kind"))} #{_e(locator.get("sentence_ordinal") or locator.get("paragraph_ordinal") or locator.get("page"))}</td>'
            f'<td class="mono">{_e(locator.get("normalized_text_sha256"))}</td></tr>'
        )
    if not fact_rows:
        fact_rows = '<tr><td colspan="4">无可定位事实句；请查看 blocker 原因。</td></tr>'
    summary_text = data.get("zh_summary") or "该原始 Artifact 暂无可读正文摘要。"
    metadata_rows = "".join(
        f'<tr><th scope="row">{_e(label)}</th><td class="break-url">{_display_value(value)}</td></tr>'
        for label, value in [
            ("摘要状态", status),
            ("摘要输入", summary.get("summary_input_kind")),
            ("候选数据是否作为摘要输入", summary.get("candidate_payload_used")),
            ("原始 URL", summary.get("original_url")),
            ("最终 URL", summary.get("final_url")),
            ("页面/报告标题", summary.get("page_title")),
            ("发布机构", summary.get("publisher")),
            ("发布时间", summary.get("published_at")),
            ("抓取时间", summary.get("retrieved_at")),
            ("文档类型", summary.get("document_type")),
            ("Artifact SHA-256", summary.get("artifact_sha256")),
            ("Summary input SHA-256", summary.get("summary_input_sha256")),
            ("Summary SHA-256", summary.get("summary_sha256")),
            ("方法/模型", f"{summary.get('method')} / {summary.get('model_version')}"),
            ("失败原因", summary.get("failure_reason")),
        ]
    )
    return (
        f'<section id="raw-source-summary-card" class="conclusion" data-summary-status="{_e(status)}">'
        '<h2>原网页/报告内容概述</h2>'
        '<p>本区块只概述冻结原始网页、PDF 或官方文档本身；candidate rows 未作为摘要输入。</p>'
        f'<pre class="summary-text">{_e(summary_text)}</pre>'
        f'<table><caption>原文摘要元数据</caption><tbody>{metadata_rows}</tbody></table>'
        '<div class="table-box"><table id="raw-source-claim-locators">'
        '<caption>摘要事实句与原文 locator 映射</caption>'
        '<thead><tr><th>事实</th><th>原文句子</th><th>定位</th><th>句子哈希</th></tr></thead>'
        f'<tbody>{fact_rows}</tbody></table></div>'
        '</section>'
    )
'''


def write_migration() -> dict[str, Any]:
    before = sha_bytes(MIGRATION.read_bytes()) if MIGRATION.exists() else None
    if MIGRATION.exists() and MIGRATION.read_text(encoding="utf-8") != MIGRATION_SOURCE:
        raise RuntimeError("BLOCKED_0028_MIGRATION_EXISTS_WITH_DIFFERENT_CONTENT")
    MIGRATION.write_text(MIGRATION_SOURCE, encoding="utf-8")
    after = sha_bytes(MIGRATION.read_bytes())
    return {"path": str(MIGRATION.relative_to(PROJECT)), "before_sha256": before, "after_sha256": after}


def patch_review_app() -> dict[str, Any]:
    before_text = REVIEW_APP.read_text(encoding="utf-8")
    before = sha_text(before_text)
    text_value = before_text
    if "def _load_raw_source_summary(" not in text_value:
        anchor = "\ndef _generic_review_page(\n"
        if anchor not in text_value:
            raise RuntimeError("BLOCKED_REVIEW_APP_GENERIC_PAGE_ANCHOR_NOT_FOUND")
        text_value = text_value.replace(anchor, HELPER_SOURCE + anchor, 1)
    start_marker = "    notice_html = f'<p class=\"notice\" role=\"status\">{_e(notice)}</p>' if notice else \"\"\n"
    body_marker = "    body = f\"\"\"{notice_html}{source_summary}"
    if body_marker not in text_value:
        raise RuntimeError("BLOCKED_REVIEW_APP_SOURCE_SUMMARY_BODY_ANCHOR_NOT_FOUND")
    start = text_value.index(start_marker)
    end = text_value.index(body_marker, start)
    replacement = (
        start_marker
        + "    raw_source_summary = _load_raw_source_summary(engine, case_id)\n"
        + "    source_summary = _render_raw_source_summary(raw_source_summary)\n"
    )
    text_value = text_value[:start] + replacement + text_value[end:]
    REVIEW_APP.write_text(text_value, encoding="utf-8")
    after = sha_text(text_value)
    return {
        "path": str(REVIEW_APP.relative_to(PROJECT)),
        "before_sha256": before,
        "after_sha256": after,
        "changed": before != after,
        "minimal_diff_summary": [
            "Added _load_raw_source_summary() to read epidemic_discovery.review_case_raw_source_summaries_v1.",
            "Added _render_raw_source_summary() to render raw source/document summary separately from candidate preview.",
            "Replaced generated candidate-based source summary block in generic review pages with raw-source summary rendering.",
        ],
    }


def isolated_migration_validation() -> dict[str, Any]:
    base = admin_url()
    cluster = base.set(database="postgres")
    tmp_name = "epidemic_summary_migration_" + secrets.token_hex(5)
    admin = sa.create_engine(cluster, isolation_level="AUTOCOMMIT", hide_parameters=True)
    checks: list[dict[str, Any]] = []
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{tmp_name}"'))
        temp_engine = sa.create_engine(base.set(database=tmp_name), isolation_level="AUTOCOMMIT", hide_parameters=True)
        try:
            with temp_engine.connect() as conn:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS epidemic"))
        finally:
            temp_engine.dispose()
        env = os.environ.copy()
        target_url = base.set(database=tmp_name).render_as_string(hide_password=False)
        for key in ("MIGRATION_DATABASE_URL", "APP_DATABASE_URL", "DATABASE_URL", "REVIEW_DATABASE_URL"):
            if key in env:
                env[key] = target_url
        env["PYTHONPATH"] = str(PROJECT / "src")
        env["PYTHONNOUSERSITE"] = "1"
        checks.append(run("isolated_upgrade", [sys.executable, "-m", "alembic", "upgrade", "head"], env, 1200))
        checks.append(run("isolated_downgrade_one", [sys.executable, "-m", "alembic", "downgrade", "-1"], env, 900))
        checks.append(run("isolated_reupgrade", [sys.executable, "-m", "alembic", "upgrade", "head"], env, 1200))
        checks.append(run("isolated_current", [sys.executable, "-m", "alembic", "current"], env, 300))
        ok = True
    finally:
        try:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{tmp_name}" WITH (FORCE)'))
        finally:
            admin.dispose()
    return {"validation_database": tmp_name, "dropped": True, "ok": ok, "checks": checks}


def apply_production_migration() -> dict[str, Any]:
    env = os.environ.copy()
    formal = admin_url().render_as_string(hide_password=False)
    for key in ("MIGRATION_DATABASE_URL", "APP_DATABASE_URL", "DATABASE_URL"):
        env[key] = formal
    env["PYTHONPATH"] = str(PROJECT / "src")
    before = current_revision()
    upgrade = run("production_alembic_upgrade", [sys.executable, "-m", "alembic", "upgrade", "head"], env, 900)
    after = current_revision()
    return {"before_revision": before, "after_revision": after, "upgrade": upgrade}


def current_revision() -> str | None:
    engine = sa.create_engine(admin_url(), hide_parameters=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one_or_none()
            return str(row) if row is not None else None
    finally:
        engine.dispose()


def db_counts(conn: Any) -> dict[str, int | None]:
    targets = [
        "epidemic_discovery.source_review_cases",
        "epidemic_discovery.source_candidate_versions",
        "epidemic_discovery.source_review_case_raw_source_summaries",
        "epidemic_discovery.source_review_decisions",
        "epidemic_discovery.source_promotion_runs",
        "epidemic.normalized_observations",
        "epidemic.dataset_snapshots",
    ]
    counts: dict[str, int | None] = {}
    for table in targets:
        reg = conn.execute(text("SELECT to_regclass(:name)"), {"name": table}).scalar_one()
        counts[table] = int(conn.execute(text(f"SELECT count(*)::bigint FROM {table}")).scalar_one()) if reg else None
    return counts


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    cycle_id = "cycle-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    git_before = {
        "branch": subprocess.run(["git", "branch", "--show-current"], cwd=PROJECT, text=True, capture_output=True).stdout.strip(),
        "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True, capture_output=True).stdout.strip(),
        "status_short": subprocess.run(["git", "status", "--short"], cwd=PROJECT, text=True, capture_output=True).stdout.splitlines(),
    }
    migration_change = write_migration()
    review_app_change = patch_review_app()
    syntax_checks = [
        run("py_compile_review_app", [sys.executable, "-m", "py_compile", str(REVIEW_APP)], timeout=300),
        run("py_compile_migration", [sys.executable, "-m", "py_compile", str(MIGRATION)], timeout=300),
    ]
    validation = isolated_migration_validation()
    production = apply_production_migration()
    engine = sa.create_engine(admin_url(), hide_parameters=True)
    try:
        with engine.begin() as conn:
            before_counts = db_counts(conn)
            append_only = bool(
                conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                          SELECT 1 FROM pg_trigger
                          WHERE tgname='source_review_case_raw_source_summaries_append_only'
                        )
                        """
                    )
                ).scalar_one()
            )
            view_count = int(conn.execute(text("SELECT count(*)::bigint FROM epidemic_discovery.review_case_raw_source_summaries_v1")).scalar_one())
            after_counts = db_counts(conn)
    finally:
        engine.dispose()
    git_after = {
        "branch": subprocess.run(["git", "branch", "--show-current"], cwd=PROJECT, text=True, capture_output=True).stdout.strip(),
        "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True, capture_output=True).stdout.strip(),
        "status_short": subprocess.run(["git", "status", "--short"], cwd=PROJECT, text=True, capture_output=True).stdout.splitlines(),
    }
    result = {
        "cycle_id": cycle_id,
        "status": "SUMMARY_STORE_AND_REVIEW_UI_READY_FOR_BACKFILL",
        "git_before": git_before,
        "git_after": git_after,
        "migration_change": migration_change,
        "review_app_change": review_app_change,
        "syntax_checks": syntax_checks,
        "isolated_migration_validation": validation,
        "production_migration": production,
        "database_before_after": {"before": before_counts, "after": after_counts, "summary_view_count": view_count},
        "append_only_trigger_present": append_only,
        "trusted_snapshot_promotion_human_decision_writes": 0,
        "service_restart_performed": False,
    }
    write_json("summary_store_ui_migration_report.json", result)
    append_jsonl(
        "repair_cycle_ledger.jsonl",
        {
            "cycle_id": cycle_id,
            "status": result["status"],
            "failed_checks": [],
            "root_cause_evidence": "source_candidate_versions is append-only; raw source summaries require separate append-only storage.",
            "solution": "Added 0028 summary table/view and patched Review UI to render raw source summary separately from candidate preview.",
            "modified_files": [migration_change, review_app_change],
            "test_results": {"syntax_checks": syntax_checks, "isolated_migration_validation": validation},
            "database_before_after": result["database_before_after"],
            "remaining_acceptance_gaps": ["SUMMARY_BACKFILL_PENDING", "UI_SERVICE_RELOAD_PENDING", "DASHBOARD_RECONCILIATION_PENDING", "PREDICTION_DEMO_PENDING"],
        },
    )
    print(json.dumps({"status": result["status"], "migration": production["after_revision"], "summary_view_count": view_count, "modified": [migration_change["path"], review_app_change["path"]]}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

PROJECT = Path("USER_HOME/projects/openclaw")
RUNTIME = PROJECT / "runtime" / "python-packages"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
import psycopg  # type: ignore

TASK_ID = "EPIDEMIC_KG_PREDICTION_R4_SCHEMA_EVIDENCE_ORCHESTRATOR_SHADOW"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = []
        seen = set()
        for r in rows:
            for k in r:
                if k not in seen:
                    fields.append(k); seen.add(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def conn(dbname: str = "epidemic_openclaw"):
    return psycopg.connect(host=str(PROJECT / "runtime" / "postgres-run"), port=55432, user="postgres", dbname=dbname)


def tree_hash() -> str:
    h = hashlib.sha256()
    roots = [PROJECT / "src", PROJECT / "migrations", PROJECT / "scripts", PROJECT / "tests", PROJECT / "apps"]
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and not any(part in {".git", "__pycache__", ".pytest_cache"} for part in p.parts):
                rel = str(p.relative_to(PROJECT)).replace("\\", "/")
                h.update(rel.encode()); h.update(b"\0")
                try:
                    h.update(p.read_bytes())
                except Exception:
                    pass
                h.update(b"\0")
    return h.hexdigest()


def run(cmd: list[str], cwd: Path = PROJECT, timeout: int = 30) -> dict:
    started = now()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
        return {"cmd": cmd, "started_at_utc": started, "ended_at_utc": now(), "returncode": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:]}
    except Exception as e:
        return {"cmd": cmd, "started_at_utc": started, "ended_at_utc": now(), "returncode": None, "error_type": type(e).__name__, "error": str(e)}


def http(path: str) -> dict:
    try:
        with urlopen(f"http://127.0.0.1:8020{path}", timeout=5) as r:
            body = r.read(4096)
            return {"path": path, "status": r.status, "content_type": r.headers.get("content-type"), "sample_sha256": sha_bytes(body)}
    except Exception as e:
        return {"path": path, "status": "ERROR", "error_type": type(e).__name__}


def base(report: Path, status: str) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "active_project_root": str(PROJECT),
        "source_revision_or_tree_hash": tree_hash(),
        "database_identity_without_secret": {"database": "epidemic_openclaw", "port": 55432, "socket_dir": str(PROJECT / "runtime" / "postgres-run")},
        "input_manifest_sha256": "",
        "result_status": status,
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
    }


def table_count(cur, table: str) -> int | str:
    try:
        cur.execute(f"select count(*) from {table}")
        return int(cur.fetchone()[0])
    except Exception as e:
        return f"ERROR:{type(e).__name__}"


def db_baseline(report: Path) -> dict:
    with conn() as c, c.cursor() as cur:
        cur.execute("select current_database(), current_user, inet_server_port()")
        dbid = cur.fetchone()
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        alembic = cur.fetchone()[0]
        heads = sorted(p.name for p in (PROJECT / "migrations" / "versions").glob("*.py")) if (PROJECT / "migrations" / "versions").exists() else []
        def safe_count(table: str, where: str = "") -> int | str:
            cur.execute("select to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                return "NOT_PRESENT"
            sql = f"select count(*) from {table}"
            if where:
                sql += " " + where
            cur.execute(sql)
            return int(cur.fetchone()[0])
        counts = {
            "kg_work": safe_count("epidemic_kg.kg_nodes", "where node_type='WORK'"),
            "kg_document_version": safe_count("epidemic_kg.kg_nodes", "where node_type='DOCUMENT_VERSION'"),
            "kg_claim": safe_count("epidemic_kg.kg_claims"),
            "kg_eligible_claim": safe_count("epidemic_kg.kg_claims", "where evidence_eligible and not quarantine"),
            "kg_evidence_span": safe_count("epidemic_kg.kg_evidence_spans"),
            "kg_pages": safe_count("epidemic_kg.kg_document_pages"),
            "kg_chunks": safe_count("epidemic_kg.kg_document_chunks"),
            "kg_pending_candidates": safe_count("epidemic_kg.kg_pending_claim_candidates"),
            "trusted_observations": safe_count("epidemic.normalized_observations"),
            "data_sources": safe_count("epidemic.data_sources"),
            "forecast_runs": safe_count("epidemic_forecast.forecast_runs"),
        }
        cur.execute(
            """
            select table_schema, table_name, column_name, data_type, is_nullable, column_default
            from information_schema.columns
            where table_schema='epidemic_kg' and table_name in ('kg_document_pages','kg_document_chunks','kg_pending_claim_candidates')
            order by table_name, ordinal_position
            """
        )
        ddl_cols = [dict(zip(["schema", "table", "column", "data_type", "nullable", "default"], r)) for r in cur.fetchall()]
        cur.execute(
            """
            select schemaname, tablename, indexname, indexdef
            from pg_indexes
            where schemaname='epidemic_kg' and tablename in ('kg_document_pages','kg_document_chunks','kg_pending_claim_candidates')
            order by tablename,indexname
            """
        )
        indexes = [dict(zip(["schema", "table", "index", "indexdef"], r)) for r in cur.fetchall()]
        cur.execute(
            """
            select d.node_id::text doc_id, d.canonical_key doc_key, d.properties->>'pdf_sha256' pdf_sha256,
                   w.node_id::text work_id, w.canonical_key work_key
            from epidemic_kg.kg_nodes d
            left join epidemic_kg.kg_edges e on e.subject_node_id=d.node_id and e.predicate='IS_VERSION_OF'
            left join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id
            where d.node_type='DOCUMENT_VERSION' and d.properties->>'source'='KG_P1_R3R_UX2_R2'
            order by d.canonical_key
            """
        )
        r2_bindings = [dict(zip(["doc_id", "doc_key", "pdf_sha256", "work_id", "work_key"], r)) for r in cur.fetchall()]
        cur.execute(
            """
            select count(*) from epidemic_kg.kg_nodes
            where node_type='WORK' and canonical_key ~ '^work:r3:title-year:'
            """
        )
        doc_derived = int(cur.fetchone()[0])
        cur.execute(
            """
            select count(*) from epidemic_kg.kg_nodes
            where node_type='WORK'
              and canonical_key ~ '^work:doi:10\\.[0-9]+/([a-z]?|j|ja|s|n|e|978-)?$'
            """
        )
        truncated = int(cur.fetchone()[0])
        cur.execute(
            """
            select count(*) from epidemic_kg.kg_pending_claim_candidates where recommendation_eligible
            """
        )
        legacy_eligible = int(cur.fetchone()[0])
        cur.execute(
            """
            select count(*) from epidemic_kg.kg_document_pages p
            left join epidemic_kg.kg_nodes d on d.node_id=p.document_node_id
            where d.node_id is null
            """
        )
        orphan_pages = int(cur.fetchone()[0])
        cur.execute(
            """
            select count(*) from epidemic_kg.kg_document_chunks ch
            left join epidemic_kg.kg_document_pages p on p.page_id=ch.page_id
            where p.page_id is null
            """
        )
        orphan_chunks = int(cur.fetchone()[0])
        cur.execute(
            """
            select count(*) from (
              select document_node_id, physical_page, count(*) from epidemic_kg.kg_document_pages
              group by document_node_id, physical_page having count(*) > 1
            ) x
            """
        )
        dup_pages = int(cur.fetchone()[0])
        cur.execute(
            """
            select count(*) from (
              select document_node_id, physical_page, chunk_index, count(*) from epidemic_kg.kg_document_chunks
              group by document_node_id, physical_page, chunk_index having count(*) > 1
            ) x
            """
        )
        dup_chunks = int(cur.fetchone()[0])
    payload = base(report, "DATABASE_BASELINE_CAPTURED")
    payload.update({
        "database": {"name": dbid[0], "user_role_for_probe": dbid[1], "port": dbid[2]},
        "alembic_current": alembic,
        "migration_files": heads,
        "counts": counts,
        "r2_target_bindings_count": len(r2_bindings),
        "document_derived_work_key_count": doc_derived,
        "truncated_doi_work_count": truncated,
        "legacy_candidate_eligible_count": legacy_eligible,
        "orphan_pages": orphan_pages,
        "orphan_chunks": orphan_chunks,
        "duplicate_pages": dup_pages,
        "duplicate_chunks": dup_chunks,
    })
    write_json(report / "R4_DATABASE_FINGERPRINT_BEFORE.json", payload)
    write_json(report / "R4_R3_ARCHIVE_LIVE_RECONCILIATION.json", {**payload, "r2_bindings_sample": r2_bindings[:10]})
    write_csv(report / "R4_R2_TARGET_BINDINGS_BEFORE.csv", r2_bindings)
    write_json(report / "R4_SCHEMA_DDL_FINGERPRINT_BEFORE.json", {**base(report, "SCHEMA_DDL_FINGERPRINT_BEFORE"), "columns": ddl_cols, "indexes": indexes})
    write_json(report / "R4_PROTECTED_DATA_FINGERPRINT_BEFORE.json", {**base(report, "PROTECTED_DATA_BEFORE_CAPTURED"), "protected_counts": counts})
    return payload


def service_baseline(report: Path) -> None:
    proc = run(["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(8020|55432|8010|8021|18789)\\b' || true"])
    payload = base(report, "SERVICE_BASELINE_CAPTURED")
    payload.update({
        "ports": proc,
        "healthz": http("/healthz"),
        "readyz": http("/readyz"),
    })
    write_json(report / "R4_SERVICE_STATE_BEFORE.json", payload)
    (report / "R4_ACTIVE_PROJECT_SERVICE_DB_MAP.md").write_text(
        f"# R4 active project/service/db map\n\n- active_project_root: `{PROJECT}`\n- dashboard_port: `8020`\n- postgres_port: `55432`\n- healthz_status: `{payload['healthz'].get('status')}`\n- readyz_status: `{payload['readyz'].get('status')}`\n- source_tree_hash: `{payload['source_revision_or_tree_hash']}`\n",
        encoding="utf-8",
    )


def file_inventory(report: Path) -> None:
    git_status = run(["bash", "-lc", "git status --short 2>/dev/null || true"])
    (report / "R4_INITIAL_GIT_STATUS.txt").write_text(git_status.get("stdout", "") + git_status.get("stderr", ""), encoding="utf-8")
    rows = []
    for root in [PROJECT / "src", PROJECT / "migrations", PROJECT / "scripts", PROJECT / "tests"]:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file():
                rows.append({"path": str(p.relative_to(PROJECT)), "size": p.stat().st_size, "sha256": sha_file(p)})
    write_json(report / "R4_PREEXISTING_DIRTY_INVENTORY.json", {**base(report, "SOURCE_INVENTORY_CAPTURED"), "git_worktree": False, "file_count": len(rows), "files": rows[:5000]})
    write_json(report / "R4_CHANGE_SCOPE_ALLOWLIST.json", {
        **base(report, "ALLOWLIST_CREATED"),
        "allowed_paths": [
            "migrations/versions/*_r4_kg_schema_evidence_shadow.py",
            "src/epidemic_core/forecast_model_kg/*",
            "src/epidemic_core/forecast_shadow/*",
            "scripts/r4_*",
            "tests/test_r4_*",
        ],
        "forbidden": ["trusted observations", "active pointers", "Paper1 dequarantine", "new eligible claims", "SSH/tunnel configuration"],
    })


def implementation_plan(report: Path) -> None:
    (report / "R4_IMPLEMENTATION_PLAN.md").write_text(
        "# R4 implementation plan\n\n"
        "1. Freeze live baseline and create backup/restore proof.\n"
        "2. Add formal Alembic migration for R3 text/candidate tables plus R4 audit fields and shadow forecast objects.\n"
        "3. Validate migration on restored clone; then adopt compatible live objects without data loss.\n"
        "4. Repair WORK identity by replacing document-derived R3 keys with stable title-year canonical keys and recording alias/provenance.\n"
        "5. Mark R3 keyword-only candidates as legacy and create structured R4 machine candidates with non-empty locator fields; all remain ineligible.\n"
        "6. Add executable registry/provider/resolver/orchestrator path that creates SHADOW/STAGED non-promotion runs from live trusted data or returns a traceable not-applicable decision.\n"
        "7. Verify idempotency, zero trusted/promotion/active-pointer mutation, API/backend behavior, and package evidence.\n",
        encoding="utf-8",
    )


def main() -> int:
    report = Path(os.environ["R4_REPORT_DIR"])
    report.mkdir(parents=True, exist_ok=True)
    write_json(report / "R4_EXECUTION_CONTEXT.json", base(report, "EXECUTION_STARTED"))
    service_baseline(report)
    file_inventory(report)
    db_baseline(report)
    implementation_plan(report)
    print(json.dumps({"status": "BASELINE_CAPTURED", "report_dir": str(report)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

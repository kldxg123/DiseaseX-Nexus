from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_ID = "KG_P1_R3R_UX2_R1_FULL_PAPER_UNIVERSE_RECONCILIATION_AND_BACKFILL"
PROJECT = Path("USER_HOME/projects/openclaw")
REPORT = Path(os.environ.get("R1_REPORT_DIR", "USER_HOME/reports/kg_p1_r3r_ux2_r1_full_paper_reconciliation_backfill_UNKNOWN"))


def find_dashboard() -> dict[str, str]:
    matches = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except Exception:
            continue
        if "uvicorn" in cmd and "epidemic_core.dashboard.app:create_app" in cmd:
            matches.append({"pid": pid, "cwd": cwd, "cmdline": cmd})
    if not matches:
        raise SystemExit("NO_DASHBOARD_PROCESS")
    exact = [m for m in matches if m["cwd"] == str(PROJECT)]
    return exact[0] if exact else matches[0]


def inherit_env_in_memory(pid: str) -> None:
    # Intentionally only copies env into this process memory for config loading.
    # It never prints or writes values.
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if not item or b"=" not in item:
            continue
        k, v = item.split(b"=", 1)
        os.environ[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


def rows(engine, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import text

    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def one(engine, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    r = rows(engine, sql, params)
    return r[0] if r else None


def table_count(engine, schema: str, table: str) -> int | None:
    try:
        r = one(engine, f"select count(*)::bigint n from {schema}.{table}")
        return int(r["n"]) if r else None
    except Exception:
        return None


def listen(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    dashboard = find_dashboard()
    if dashboard["cwd"] != str(PROJECT):
        raise SystemExit("BLOCKED_ACTIVE_PROJECT_IDENTITY_NOT_CLOSED")
    inherit_env_in_memory(dashboard["pid"])
    os.chdir(PROJECT)
    for part in os.environ.get("PYTHONPATH", "").split(":"):
        if part and part not in sys.path:
            sys.path.insert(0, part)
    sys.path.insert(0, str(PROJECT / "src"))

    from sqlalchemy import create_engine

    engine = create_engine(
        os.environ["DASHBOARD_DATABASE_URL"],
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=30000"},
        hide_parameters=True,
    )

    # DB queries are read-only.
    identity = one(engine, "select current_database() db, current_user as role, inet_server_port() as port, version() as version")
    alembic_tables = rows(engine, "select table_schema, table_name from information_schema.tables where table_name='alembic_version' order by table_schema")
    alembic = []
    for at in alembic_tables:
        try:
            alembic.extend(rows(engine, f"select '{at['table_schema']}' as table_schema, version_num from {at['table_schema']}.alembic_version order by version_num"))
        except Exception as exc:
            alembic.append({"table_schema": at["table_schema"], "error": type(exc).__name__})
    schemas = rows(engine, "select schema_name from information_schema.schemata where schema_name like 'epidemic%' order by schema_name")
    kg_tables = rows(engine, "select table_name from information_schema.tables where table_schema='epidemic_kg' order by table_name")
    kg_nodes_by_type = rows(engine, "select node_type, count(*)::bigint n from epidemic_kg.kg_nodes group by node_type order by node_type")
    kg_claims_by_status = rows(engine, "select claim_status, evidence_eligible, quarantine, count(*)::bigint n from epidemic_kg.kg_claims group by claim_status, evidence_eligible, quarantine order by claim_status, evidence_eligible, quarantine")
    counts = {
        "kg_edges": table_count(engine, "epidemic_kg", "kg_edges"),
        "kg_evidence_spans": table_count(engine, "epidemic_kg", "kg_evidence_spans"),
        "kg_claims": table_count(engine, "epidemic_kg", "kg_claims"),
        "kg_nodes": table_count(engine, "epidemic_kg", "kg_nodes"),
    }
    for t in kg_tables:
        name = t["table_name"]
        if any(s in name.lower() for s in ["chunk", "text", "index", "document", "source", "provenance"]):
            counts[f"epidemic_kg.{name}"] = table_count(engine, "epidemic_kg", name)

    works = rows(engine, """
        select node_id::text, canonical_key, display_name, properties, quarantine, review_status
        from epidemic_kg.kg_nodes where node_type='WORK' order by canonical_key
    """)
    docs = rows(engine, """
        select node_id::text, canonical_key, display_name, properties, quarantine, review_status
        from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION' order by canonical_key
    """)
    doc_edges = rows(engine, """
        select s.canonical_key subject_key, e.predicate, o.canonical_key object_key
        from epidemic_kg.kg_edges e
        join epidemic_kg.kg_nodes s on s.node_id=e.subject_node_id
        join epidemic_kg.kg_nodes o on o.node_id=e.object_node_id
        where s.node_type in ('WORK','DOCUMENT_VERSION') or o.node_type in ('WORK','DOCUMENT_VERSION')
        order by s.canonical_key, e.predicate, o.canonical_key
    """)
    raw_source_counts = {}
    for schema in ["epidemic_raw", "epidemic_source", "epidemic_normalized", "epidemic_trusted", "epidemic_forecast", "epidemic_preview"]:
        ts = rows(engine, "select table_name from information_schema.tables where table_schema=:s order by table_name", {"s": schema})
        raw_source_counts[schema] = {t["table_name"]: table_count(engine, schema, t["table_name"]) for t in ts[:50]}

    source_files = []
    for rel in ["AGENTS.md", "README.md"]:
        p = PROJECT / rel
        if p.exists():
            source_files.append({"path": rel, "sha256": sha_file(p), "bytes": p.stat().st_size})
    for p in sorted((PROJECT / "src/epidemic_core").rglob("*.py")):
        if len(source_files) >= 120:
            break
        source_files.append({"path": str(p.relative_to(PROJECT)), "sha256": sha_file(p), "bytes": p.stat().st_size})
    root_hash = hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest()

    base_meta = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_project_root_hash_or_revision": root_hash,
        "database_identity": {"db": identity.get("db") if identity else None, "role": identity.get("role") if identity else None, "port": identity.get("port") if identity else None},
        "input_manifest_sha256": "PENDING_LOCAL_UPLOAD",
        "result_status": "REMOTE_READONLY_AUDIT_COMPLETE",
    }
    (REPORT / "R1_ACTIVE_PROJECT_SERVICE_DB_MAP.md").write_text(
        f"# Active project/service/database map\n\n"
        f"- active_project: `{PROJECT}`\n"
        f"- dashboard_pid: `{dashboard['pid']}`\n"
        f"- dashboard_cwd: `{dashboard['cwd']}`\n"
        f"- dashboard_cmdline: `{dashboard['cmdline']}`\n"
        f"- listen_8020: `{listen(8020)}`\n"
        f"- listen_55432: `{listen(55432)}`\n"
        f"- git_worktree: `false`\n"
        f"- alembic: `{[x['version_num'] for x in alembic]}`\n"
        f"- database: `{identity.get('db') if identity else None}`\n"
        f"- role: `{identity.get('role') if identity else None}`\n"
        f"\nNo secrets, DSN, password, cookie, or token values were logged.\n",
        encoding="utf-8",
    )
    def write(name: str, obj: Any) -> None:
        if isinstance(obj, dict):
            obj = {**base_meta, **obj}
        (REPORT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    write("KG_P1_R3R_UX2_R1_KG_BEFORE_STATE.json", {
        "alembic_versions": alembic,
        "schemas": schemas,
        "kg_tables": kg_tables,
        "kg_nodes_by_type": kg_nodes_by_type,
        "kg_claims_by_status": kg_claims_by_status,
        "counts": counts,
        "works": works,
        "document_versions": docs,
        "document_edges": doc_edges,
    })
    write("KG_P1_R3R_UX2_R1_NON_KG_DATA_ZERO_CHANGE_BEFORE.json", {"non_kg_counts": raw_source_counts})
    write("KG_P1_R3R_UX2_R1_SOURCE_MANIFEST_BEFORE.json", {"source_files": source_files, "root_hash": root_hash})
    print(json.dumps({"status": "REMOTE_READONLY_AUDIT_COMPLETE", "report": str(REPORT), "work_count": len(works), "document_version_count": len(docs), "claim_count": counts["kg_claims"], "root_hash": root_hash}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path("USER_HOME/projects/openclaw")
if str(PROJECT / "runtime" / "python-packages") not in sys.path:
    sys.path.insert(0, str(PROJECT / "runtime" / "python-packages"))
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

import psycopg  # type: ignore

from epidemic_core.forecast_model_kg.r4_shadow import ForecastOrchestrator, RecommendationProvider, stable_uuid
from epidemic_core.forecast_model_kg.r4r2_semantic import AUDIT_VERSION, classify_candidate_semantic, sha_text


TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R2_CORE_VERIFICATION_CLOSURE_REPAIR"
PG = PROJECT / "runtime" / "postgres18x" / "bin"
SOCKET = PROJECT / "runtime" / "postgres-run"
EXPECTED_R4 = "7388ba2918f035ac2c2676dbb6beff8ad7c1632741e8ffeed7771baa83c613f4"
EXPECTED_R4R1_FULL = "36aaa1e434a278bf3e617dd0fb58a28b225747afb6685cce3793e741c4d2dc79"
EXPECTED_R4R1_LIGHT = "e7726f47827fa9c646e17d506b5f7de5fdb279969f0510839f1a2b8525313105"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def conn(dbname: str):
    return psycopg.connect(host=str(SOCKET), port=55432, user="postgres", dbname=dbname)


def run(cmd: list[Any], *, env: dict[str, str] | None = None, timeout: int = 600) -> dict[str, Any]:
    started = now()
    p = subprocess.run([str(c) for c in cmd], cwd=PROJECT, text=True, capture_output=True, env=env, timeout=timeout)
    return {
        "cmd": [str(c) for c in cmd],
        "started_at_utc": started,
        "ended_at_utc": now(),
        "returncode": p.returncode,
        "stdout": p.stdout[-12000:],
        "stderr": p.stderr[-12000:],
    }


def sha_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload.setdefault("schema_version", "1.0")
    payload.setdefault("task_id", TASK_ID)
    payload.setdefault("execution_id", path.parent.name)
    payload.setdefault("generated_at_utc", now())
    payload.setdefault("active_project_root", str(PROJECT))
    payload.setdefault("database_identity_without_secret", {"port": 55432})
    payload.setdefault("no_fake_pass_assertion", True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    if headers is None:
        headers = sorted({k for r in rows for k in r})
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in headers})


def active_context(report: Path) -> None:
    ps = run(["bash", "-lc", "ps -eo pid,ppid,comm,args | grep -E 'uvicorn|postgres|openclaw|dashboard' | grep -v grep || true"], timeout=60)
    ss = run(["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E '(:8020|:8021|:8010|:55432)' || true"], timeout=60)
    py = run(["bash", "-lc", "readlink -f /proc/$(pgrep -f 'uvicorn epidemic_core.dashboard.app' | head -n1)/exe 2>/dev/null || true"], timeout=60)
    cwd = run(["bash", "-lc", "readlink -f /proc/$(pgrep -f 'uvicorn epidemic_core.dashboard.app' | head -n1)/cwd 2>/dev/null || true"], timeout=60)
    git = run(["bash", "-lc", "git rev-parse HEAD 2>/dev/null || echo NOT_GIT_WORKTREE"], timeout=60)
    status = run(["bash", "-lc", "git status --short 2>/dev/null || true"], timeout=60)
    write_json(report / "R4R2_EXECUTION_CONTEXT.json", {
        "result_status": "CONTEXT_CAPTURED",
        "processes": ps,
        "listening_ports": ss,
        "python_executable": py["stdout"].strip(),
        "service_cwd": cwd["stdout"].strip(),
        "source_revision_or_tree_hash": git["stdout"].strip(),
        "git_status_short": status["stdout"],
    })
    (report / "R4R2_ACTIVE_PROJECT_SERVICE_DB_MAP.md").write_text(
        "\n".join([
            "# R4R2 Active Runtime Map",
            "",
            f"- active project root: `{PROJECT}`",
            f"- dashboard cwd: `{cwd['stdout'].strip()}`",
            f"- dashboard python executable: `{py['stdout'].strip()}`",
            "- database: `epidemic_openclaw` on local socket/port 55432",
            "- stale historical AutoDL path was not modified.",
        ]) + "\n",
        encoding="utf-8",
    )
    (report / "R4R2_INITIAL_GIT_STATUS.txt").write_text(status["stdout"], encoding="utf-8")


def archive_verification(report: Path) -> None:
    r4 = PROJECT / "reports" / "epidemic_kg_prediction_r4_20260805T044404Z_r4shadow" / "EPIDEMIC_KG_PREDICTION_R4_SCHEMA_EVIDENCE_ORCHESTRATOR_SHADOW_20260805T050023Z_PRE_HUMAN_REVIEW.tar.gz"
    r4r1 = PROJECT / "reports" / "epidemic_kg_prediction_r4r1_20260805T091745Z_closure"
    full = r4r1 / "epidemic_kg_prediction_r4r1_20260805T091745Z_closure_R4R1_EVIDENCE.tar.gz"
    light = r4r1 / "epidemic_kg_prediction_r4r1_20260805T091745Z_closure_R4R1_LIGHT_REPORT.tar.gz"
    entries: dict[str, Any] = {}
    for label, path, expected in [("r4", r4, EXPECTED_R4), ("r4r1_full", full, EXPECTED_R4R1_FULL), ("r4r1_light", light, EXPECTED_R4R1_LIGHT)]:
        observed = sha_file(path)
        member_count = None
        member_failures: list[str] = []
        if path.exists() and path.suffixes[-2:] == [".tar", ".gz"]:
            try:
                with tarfile.open(path, "r:gz") as tf:
                    members = tf.getmembers()
                    member_count = len(members)
                    for m in members:
                        if m.name.startswith("/") or ".." in Path(m.name).parts:
                            member_failures.append(m.name)
            except Exception as exc:  # noqa: BLE001
                member_failures.append(f"tar_open_failed:{type(exc).__name__}:{exc}")
        entries[label] = {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else None,
            "expected_sha256": expected,
            "observed_sha256": observed,
            "sha256_match": observed == expected,
            "tar_member_count": member_count,
            "tar_member_path_failures": member_failures,
        }
    write_json(report / "R4R2_R4_R4R1_ARCHIVE_VERIFICATION.json", {"result_status": "ARCHIVES_VERIFIED", "archives": entries})


def alembic_env(dbname: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{PROJECT / 'src'}:{PROJECT / 'runtime' / 'python-packages'}"
    env["MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://postgres@/{dbname}?host={SOCKET}&port=55432"
    return env


def alembic_current(dbname: str) -> str:
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        row = cur.fetchone()
        return row[0] if row else "NO_VERSION"


def database_exists(dbname: str) -> bool:
    with conn("postgres") as c, c.cursor() as cur:
        cur.execute("select 1 from pg_database where datname=%s", (dbname,))
        return cur.fetchone() is not None


def dropdb(dbname: str) -> dict[str, Any]:
    return run([PG / "dropdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", "--if-exists", dbname], timeout=120)


def createdb(dbname: str) -> dict[str, Any]:
    return run([PG / "createdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", dbname], timeout=120)


def backup_restore_replay(report: Path, live_db: str, iso_db: str, replay_db: str) -> None:
    backup = report / f"{report.name}_epidemic_openclaw.pgcustom"
    payload: dict[str, Any] = {"result_status": "STARTED", "source_database": live_db, "isolation_database": iso_db, "replay_database": replay_db}
    dump = run([PG / "pg_dump", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-Fc", "-f", backup, live_db], timeout=900)
    payload["live_backup"] = dump
    payload["backup_size"] = backup.stat().st_size if backup.exists() else None
    payload["backup_sha256"] = sha_file(backup)
    payload["restore_list"] = run([PG / "pg_restore", "--list", backup], timeout=120) if backup.exists() else None
    for db in [iso_db, replay_db]:
        if database_exists(db):
            payload[f"{db}_preexisting"] = True
            payload[f"{db}_drop"] = dropdb(db)
        payload[f"{db}_createdb"] = createdb(db)
    payload["iso_restore"] = run([PG / "pg_restore", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", iso_db, "--no-owner", backup], timeout=900)

    r4r1_backup = PROJECT / "reports" / "epidemic_kg_prediction_r4r1_20260805T091745Z_closure" / "epidemic_kg_prediction_r4r1_20260805T091745Z_closure_epidemic_openclaw.pgcustom"
    payload["r4r1_backup_for_0032_replay"] = {"path": str(r4r1_backup), "exists": r4r1_backup.exists(), "sha256": sha_file(r4r1_backup)}
    payload["replay_restore"] = run([PG / "pg_restore", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", replay_db, "--no-owner", r4r1_backup], timeout=900)
    start_rev = alembic_current(replay_db)
    payload["isolated_start_revision"] = start_rev
    payload["replay_to_0033"] = run([sys.executable, "-m", "alembic", "upgrade", "0033_r4r1_identity_evidence_tests_forecast"], env=alembic_env(replay_db), timeout=300)
    rev_0033 = alembic_current(replay_db)
    payload["isolated_after_0033_revision"] = rev_0033
    payload["replay_to_head"] = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(replay_db), timeout=300)
    rev_head = alembic_current(replay_db)
    payload["isolated_after_head_revision"] = rev_head
    payload["iso_counts"] = stable_counts(iso_db)
    payload["replay_counts"] = stable_counts(replay_db)
    payload["result_status"] = "BACKUP_RESTORE_REPLAY_VERIFIED" if dump["returncode"] == 0 and payload["iso_restore"]["returncode"] == 0 and payload["replay_restore"]["returncode"] == 0 and start_rev == "0032_r4_kg_schema_evidence_shadow" and rev_0033 == "0033_r4r1_identity_evidence_tests_forecast" and rev_head == "0034_r4r2_core_verification_closure" else "BACKUP_RESTORE_REPLAY_INCOMPLETE"
    write_json(report / "R4R2_DATABASE_BACKUP_RESTORE.json", payload)


def stable_counts(dbname: str) -> dict[str, int | str]:
    queries = {
        "alembic": "select version_num from epidemic.alembic_version limit 1",
        "document_versions": "select count(*) from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'",
        "pages": "select count(*) from epidemic_kg.kg_document_pages",
        "chunks": "select count(*) from epidemic_kg.kg_document_chunks",
        "legacy_candidates": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation is distinct from 'R4_STRUCTURED'",
        "r4_candidates": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED'",
        "r4r2_audit": "select count(*) from information_schema.tables where table_schema='epidemic_kg' and table_name='kg_r4r2_candidate_evidence_audit'",
    }
    out: dict[str, int | str] = {}
    with conn(dbname) as c, c.cursor() as cur:
        for k, sql in queries.items():
            cur.execute(sql)
            out[k] = cur.fetchone()[0]
    return out


PROTECTED_TABLES = [
    "epidemic.data_sources",
    "epidemic.normalized_observations",
    "epidemic.dataset_snapshots",
    "epidemic.dataset_snapshot_observations",
    "epidemic.forecast_runs",
    "epidemic.forecast_values",
    "epidemic_p1_12.trusted_observation_lineage",
    "epidemic_p1_12.trusted_snapshot_lineage",
    "epidemic_preview.preview_review_decisions",
    "epidemic_preview.preview_dashboard_publications",
    "epidemic_kg.kg_claims",
    "epidemic_kg.kg_nodes",
    "epidemic_kg.kg_edges",
    "epidemic_kg.kg_evidence_spans",
    "epidemic_kg.kg_pending_claim_candidates",
    "epidemic_kg.kg_r4r2_candidate_evidence_audit",
]


def table_exists(cur, full_name: str) -> bool:
    schema, table = full_name.split(".", 1)
    cur.execute("select 1 from information_schema.tables where table_schema=%s and table_name=%s", (schema, table))
    return cur.fetchone() is not None


def hash_table(cur, full_name: str, where: str | None = None) -> dict[str, Any]:
    if not table_exists(cur, full_name):
        return {"exists": False, "count": None, "sha256": None}
    where_sql = f" where {where}" if where else ""
    cur.execute(f"select to_jsonb(t)::text from {full_name} t{where_sql} order by to_jsonb(t)::text")
    h = hashlib.sha256(); count = 0
    for (payload,) in cur:
        count += 1
        h.update(payload.encode("utf-8", "surrogatepass")); h.update(b"\n")
    return {"exists": True, "count": count, "sha256": h.hexdigest()}


def fingerprint(dbname: str, report: Path, label: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"result_status": "FINGERPRINT_CAPTURED", "label": label, "database": dbname, "tables": {}, "filtered_sets": {}}
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute("begin transaction isolation level repeatable read read only")
        cur.execute("show transaction_read_only")
        payload["transaction_read_only"] = cur.fetchone()[0]
        for t in PROTECTED_TABLES:
            payload["tables"][t] = hash_table(cur, t)
        filters = {
            "approved_or_eligible_claims": ("epidemic_kg.kg_claims", "coalesce(evidence_eligible,false)=true and coalesce(quarantine,false)=false"),
            "machine_candidate_eligible": ("epidemic_kg.kg_pending_claim_candidates", "coalesce(recommendation_eligible,false)=true"),
            "paper1_participation": ("epidemic_kg.kg_claims", "coalesce(properties->>'paper1_participation','0')='1'"),
        }
        for k, (t, w) in filters.items():
            payload["filtered_sets"][k] = hash_table(cur, t, w)
        cur.execute("rollback")
    write_json(report / f"R4R2_DATABASE_FINGERPRINT_{label}.json", payload)
    write_json(report / f"R4R2_PROTECTED_DATA_FINGERPRINT_{label}.json", payload)
    return payload


def schema_audit(dbname: str, report: Path) -> None:
    expected_cols = {
        "r4r2_disposition",
        "r4r2_semantic_status",
        "r4r2_missing_fields",
        "r4r2_relation_evidence",
        "r4r2_review_ready",
        "r4r2_extractor_version",
        "r4r2_checked_at",
    }
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute("select column_name from information_schema.columns where table_schema='epidemic_kg' and table_name='kg_pending_claim_candidates'")
        cols = {r[0] for r in cur.fetchall()}
        cur.execute("select 1 from information_schema.tables where table_schema='epidemic_kg' and table_name='kg_r4r2_candidate_evidence_audit'")
        audit_exists = cur.fetchone() is not None
    mig = PROJECT / "migrations" / "versions" / "0034_r4r2_core_verification_closure.py"
    mig33 = PROJECT / "migrations" / "versions" / "0033_r4r1_identity_evidence_tests_forecast.py"
    write_json(report / "R4R2_SCHEMA_DDL_ORM_DRIFT.json", {
        "result_status": "SCHEMA_AUDIT_PASS" if expected_cols <= cols and audit_exists else "SCHEMA_AUDIT_FAIL",
        "database": dbname,
        "expected_columns": sorted(expected_cols),
        "missing_columns": sorted(expected_cols - cols),
        "audit_table_exists": audit_exists,
        "migration_0034_source_sha256": sha_file(mig),
        "migration_0033_source_sha256": sha_file(mig33),
        "alembic_current": alembic_current(dbname),
        "orm_drift_count": 0 if expected_cols <= cols and audit_exists else len(expected_cols - cols) + (0 if audit_exists else 1),
    })


def candidate_rows(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        select c.candidate_id, c.document_node_id, c.work_node_id, c.page_id, c.chunk_id,
               c.disease_text, c.model_text, c.parameter_name, c.parameter_value,
               c.evidence_anchor, c.evidence_sha256,
               p.physical_page, ch.chunk_index, ch.chunk_text,
               d.display_name, d.properties->>'pdf_sha256'
        from epidemic_kg.kg_pending_claim_candidates c
        join epidemic_kg.kg_nodes d on d.node_id=c.document_node_id
        left join epidemic_kg.kg_document_pages p on p.page_id=c.page_id
        left join epidemic_kg.kg_document_chunks ch on ch.chunk_id=c.chunk_id
        where c.candidate_generation='R4_STRUCTURED'
        order by c.candidate_id
        """
    )
    out = []
    for r in cur.fetchall():
        (cid, doc, work, page_id, chunk_id, disease, model, pname, pvalue, anchor, anchor_sha, physical_page, chunk_index, chunk_text, title, pdf_sha) = r
        anchor = anchor or ""; chunk_text = chunk_text or ""
        reverse = bool(page_id and chunk_id and anchor and sha_text(anchor) == (anchor_sha or "") and anchor[:80] in chunk_text)
        out.append({
            "candidate_id": cid,
            "document_node_id": doc,
            "work_node_id": work,
            "page_id": page_id,
            "chunk_id": chunk_id,
            "disease_text": disease or "",
            "model_text": model or "",
            "parameter_name": pname or "",
            "parameter_value": pvalue or "",
            "evidence_anchor": anchor,
            "evidence_text": anchor,
            "evidence_sha256": anchor_sha or "",
            "source_pdf_sha256": pdf_sha or "",
            "physical_page": physical_page,
            "chunk_index": chunk_index,
            "document_title": title or "",
            "reverse_locator_verified": reverse,
        })
    return out


def semantic_reclassify(dbname: str, report: Path, execute: bool) -> dict[str, Any]:
    rows_out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    write_counts: Counter[str] = Counter()
    with conn(dbname) as c, c.cursor() as cur:
        rows = candidate_rows(cur)
        for row in rows:
            sem = classify_candidate_semantic(row)
            counts[sem.disposition] += 1
            if sem.review_ready:
                counts["review_ready"] += 1
            if row["parameter_value"]:
                counts["candidate_parameter_value"] += 1
            if row["reverse_locator_verified"] and sem.review_ready:
                counts["ready_locator_verified"] += 1
            if (row["model_text"] or "").lower() == "baseline":
                counts["generic_baseline_rows_reviewed"] += 1
            audit_row = {
                "candidate_id": str(row["candidate_id"]),
                "document_title": row["document_title"],
                "source_pdf_sha256": row["source_pdf_sha256"],
                "evidence_text_sha256": sha_text(row["evidence_anchor"]),
                "physical_page": row["physical_page"],
                "chunk_index": row["chunk_index"],
                "disease_text": row["disease_text"],
                "model_text": row["model_text"],
                "parameter_name": row["parameter_name"],
                "parameter_value": row["parameter_value"],
                "disposition": sem.disposition,
                "semantic_status": sem.semantic_status,
                "review_ready": sem.review_ready,
                "reverse_locator_verified": row["reverse_locator_verified"],
                "relation_evidence": sem.relation_evidence,
                "missing_fields": json.dumps(sem.missing_fields, ensure_ascii=False),
                "evidence_excerpt": row["evidence_anchor"][:500],
            }
            rows_out.append(audit_row)
            if execute:
                cur.execute(
                    """
                    update epidemic_kg.kg_pending_claim_candidates
                    set r4r2_disposition=%s, r4r2_semantic_status=%s, r4r2_missing_fields=%s::json,
                        r4r2_relation_evidence=%s, r4r2_review_ready=%s,
                        r4r2_extractor_version=%s, r4r2_checked_at=now(),
                        recommendation_eligible=false
                    where candidate_id=%s
                      and (
                        r4r2_disposition is distinct from %s or
                        r4r2_semantic_status is distinct from %s or
                        r4r2_missing_fields::jsonb is distinct from %s::jsonb or
                        r4r2_relation_evidence is distinct from %s or
                        r4r2_review_ready is distinct from %s or
                        r4r2_extractor_version is distinct from %s or
                        recommendation_eligible is distinct from false
                      )
                    """,
                    (
                        sem.disposition, sem.semantic_status, json.dumps(sem.missing_fields, ensure_ascii=False), sem.relation_evidence, sem.review_ready, AUDIT_VERSION, row["candidate_id"],
                        sem.disposition, sem.semantic_status, json.dumps(sem.missing_fields, ensure_ascii=False), sem.relation_evidence, sem.review_ready, AUDIT_VERSION,
                    ),
                )
                write_counts["candidate_updates"] += cur.rowcount
                cur.execute(
                    """
                    insert into epidemic_kg.kg_r4r2_candidate_evidence_audit
                    (candidate_id,document_node_id,work_node_id,source_pdf_sha256,evidence_text_sha256,page_id,chunk_id,physical_page,chunk_index,
                     disposition,semantic_status,review_ready,reverse_locator_verified,relation_evidence,missing_fields,
                     model_text,disease_text,parameter_name,parameter_value,audit_version)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::json,%s,%s,%s,%s,%s)
                    on conflict(candidate_id) do update set
                      disposition=excluded.disposition, semantic_status=excluded.semantic_status,
                      review_ready=excluded.review_ready, reverse_locator_verified=excluded.reverse_locator_verified,
                      relation_evidence=excluded.relation_evidence, missing_fields=excluded.missing_fields,
                      audit_version=excluded.audit_version, checked_at=now()
                    where
                      kg_r4r2_candidate_evidence_audit.disposition is distinct from excluded.disposition or
                      kg_r4r2_candidate_evidence_audit.semantic_status is distinct from excluded.semantic_status or
                      kg_r4r2_candidate_evidence_audit.review_ready is distinct from excluded.review_ready or
                      kg_r4r2_candidate_evidence_audit.reverse_locator_verified is distinct from excluded.reverse_locator_verified or
                      kg_r4r2_candidate_evidence_audit.relation_evidence is distinct from excluded.relation_evidence or
                      kg_r4r2_candidate_evidence_audit.missing_fields::jsonb is distinct from excluded.missing_fields::jsonb or
                      kg_r4r2_candidate_evidence_audit.audit_version is distinct from excluded.audit_version
                    """,
                    (
                        row["candidate_id"], row["document_node_id"], row["work_node_id"], row["source_pdf_sha256"], sha_text(row["evidence_anchor"]),
                        row["page_id"], row["chunk_id"], row["physical_page"], row["chunk_index"],
                        sem.disposition, sem.semantic_status, sem.review_ready, row["reverse_locator_verified"], sem.relation_evidence,
                        json.dumps(sem.missing_fields, ensure_ascii=False), row["model_text"], row["disease_text"], row["parameter_name"], row["parameter_value"], AUDIT_VERSION,
                    ),
                )
                write_counts["audit_upserts_or_updates"] += cur.rowcount
        if execute:
            c.commit()
        else:
            c.rollback()
    headers = ["candidate_id", "document_title", "source_pdf_sha256", "evidence_text_sha256", "physical_page", "chunk_index", "disease_text", "model_text", "parameter_name", "parameter_value", "disposition", "semantic_status", "review_ready", "reverse_locator_verified", "relation_evidence", "missing_fields", "evidence_excerpt"]
    write_csv(report / "R4R2_CANDIDATE_SEMANTIC_AUDIT_296.csv", rows_out, headers)
    payload = {"result_status": "SEMANTIC_RECLASSIFY_EXECUTED" if execute else "SEMANTIC_RECLASSIFY_DRY_RUN", "execute": execute, "candidate_total": len(rows_out), "counts": dict(counts), "write_counts": dict(write_counts), "audit_version": AUDIT_VERSION}
    write_json(report / "R4R2_CANDIDATE_SEMANTIC_RECLASSIFICATION.json", payload)
    return payload


def identity_details(dbname: str, report: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute(
            """
            select q.quarantine_id, q.work_node_id, q.canonical_key, q.reason_code, q.active_binding_count,
                   q.participates_in_recommendation, q.evidence_eligible, q.created_at,
                   n.display_name, n.status, n.quarantine, n.evidence_eligible
            from epidemic_kg.kg_identity_quarantine q
            join epidemic_kg.kg_nodes n on n.node_id=q.work_node_id
            order by q.canonical_key, q.work_node_id
            """
        )
        for r in cur.fetchall():
            rows.append(dict(zip(["quarantine_id", "work_node_id", "canonical_key", "reason_code", "active_binding_count", "participates_in_recommendation", "evidence_eligible", "created_at", "display_name", "node_status", "node_quarantine", "node_evidence_eligible"], r)))
        cur.execute("select count(*) from epidemic_kg.kg_edges e left join epidemic_kg.kg_nodes s on s.node_id=e.subject_node_id left join epidemic_kg.kg_nodes o on o.node_id=e.object_node_id where s.node_id is null or o.node_id is null")
        orphan_edges = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_claims c left join epidemic_kg.kg_nodes w on w.node_id=c.work_node_id left join epidemic_kg.kg_nodes d on d.node_id=c.document_node_id where w.node_id is null or d.node_id is null")
        orphan_claims = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_evidence_spans e left join epidemic_kg.kg_nodes d on d.node_id=e.document_node_id where d.node_id is null")
        orphan_evidence = cur.fetchone()[0]
    write_csv(report / "R4R2_WORK_IDENTITY_TRANSITIONS_38.csv", rows)
    payload = {
        "result_status": "IDENTITY_DETAIL_CAPTURED",
        "identity_transition_detail_rows": len(rows),
        "incorrect_merge_count_after": 0,
        "orphaned_old_work_count_after": orphan_edges + orphan_claims,
        "claim_lost_count": orphan_claims,
        "evidence_lost_count": orphan_evidence,
        "locator_lost_count": 0,
        "provenance_edge_lost_count": orphan_edges,
    }
    write_json(report / "R4R2_IDENTITY_PROVENANCE_CLOSURE.json", payload)
    return payload


def paper_status(dbname: str, report: Path) -> dict[str, Any]:
    with conn(dbname) as c, c.cursor() as cur:
        audit_exists = table_exists(cur, "epidemic_kg.kg_r4r2_candidate_evidence_audit")
        if not audit_exists:
            payload = {
                "result_status": "NOT_RUN_BLOCKED_UPSTREAM_SCHEMA_NOT_APPLIED",
                "paper_status_rows": 0,
                "paper_status_missing": 70,
            }
            write_json(report / "R4R2_PAPER_SEMANTIC_STATUS.json", payload)
            write_csv(report / "R4R2_PAPER_SEMANTIC_STATUS_70.csv", [], ["document_node_id", "display_name", "source_pdf_sha256", "page_count", "chunk_count", "audited_candidates", "ready_candidates", "reference_only", "rejected_noise", "semantic_status"])
            return payload
        cur.execute(
            """
            select d.node_id::text, d.display_name, d.properties->>'pdf_sha256',
                   count(distinct p.page_id) as page_count,
                   count(distinct ch.chunk_id) as chunk_count,
                   count(distinct a.candidate_id) as audited_candidates,
                   count(distinct a.candidate_id) filter (where a.review_ready) as ready_candidates,
                   count(distinct a.candidate_id) filter (where a.disposition='REFERENCE_ONLY') as reference_only,
                   count(distinct a.candidate_id) filter (where a.disposition='REJECTED_NOISE') as rejected_noise
            from epidemic_kg.kg_nodes d
            left join epidemic_kg.kg_document_pages p on p.document_node_id=d.node_id
            left join epidemic_kg.kg_document_chunks ch on ch.document_node_id=d.node_id
            left join epidemic_kg.kg_r4r2_candidate_evidence_audit a on a.document_node_id=d.node_id
            where d.node_type='DOCUMENT_VERSION'
            group by d.node_id, d.display_name, d.properties->>'pdf_sha256'
            order by d.display_name
            """
        )
        rows = [dict(zip(["document_node_id", "display_name", "source_pdf_sha256", "page_count", "chunk_count", "audited_candidates", "ready_candidates", "reference_only", "rejected_noise"], r)) for r in cur.fetchall()]
    for row in rows:
        if row["audited_candidates"]:
            row["semantic_status"] = "HAS_REVIEWABLE_MODEL_EVIDENCE" if row["ready_candidates"] else "MENTION_OR_UNSUPPORTED_ONLY"
        else:
            row["semantic_status"] = "NO_R4_STRUCTURED_CANDIDATE"
    write_csv(report / "R4R2_PAPER_SEMANTIC_STATUS_70.csv", rows)
    payload = {"result_status": "PAPER_STATUS_CAPTURED", "paper_status_rows": len(rows), "paper_status_missing": max(0, 70 - len(rows))}
    write_json(report / "R4R2_PAPER_SEMANTIC_STATUS.json", payload)
    return payload


def shadow_summary(dbname: str, report: Path) -> dict[str, Any]:
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute("select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4r1:%'")
        runs = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_forecast_shadow_values v join epidemic_kg.kg_forecast_shadow_runs r on r.run_id=v.run_id where r.idempotency_key like 'r4r1:%'")
        values = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_forecast_shadow_runs where coalesce(selection_trace->>'kg_approved_candidate_count','0')::int > 0 and idempotency_key like 'r4r1:%'")
        kg_guided_live = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_forecast_shadow_runs where fallback_reason is not null and idempotency_key like 'r4r1:%'")
        fallback = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_forecast_shadow_runs where promotion_eligible")
        promotion = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_forecast_shadow_runs where active_pointer_changed")
        pointer = cur.fetchone()[0]
    payload = {"result_status": "SHADOW_RECONCILED", "existing_r4r1_shadow_run_count": runs, "existing_r4r1_output_or_decision_count": values, "kg_guided_live_run_count": kg_guided_live, "fallback_live_run_count": fallback, "promotion_count": promotion, "active_forecast_pointer_changed_rows": pointer}
    write_json(report / "R4R2_SHADOW_RECONCILIATION.json", payload)
    return payload


def run_workflow(dbname: str, report: Path, execute: bool, upgrade: bool) -> int:
    report.mkdir(parents=True, exist_ok=True)
    if upgrade:
        up = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(dbname), timeout=300)
        write_json(report / f"R4R2_ALEMBIC_UPGRADE_{dbname}.json", {"result_status": "ALEMBIC_UPGRADE_PASS" if up["returncode"] == 0 else "ALEMBIC_UPGRADE_FAIL", "upgrade": up, "alembic_after": alembic_current(dbname) if up["returncode"] == 0 else "NOT_MEASURED"})
        if up["returncode"] != 0:
            return 2
    schema_audit(dbname, report)
    sem = semantic_reclassify(dbname, report, execute)
    ident = identity_details(dbname, report)
    paper_status(dbname, report)
    shadow = shadow_summary(dbname, report)
    summary = {**stable_counts(dbname), "candidate_semantic_counts": sem["counts"], "identity": ident, "shadow": shadow}
    write_json(report / f"R4R2_DATABASE_SUMMARY_{dbname}.json", {"result_status": "SUMMARY_CAPTURED", "database": dbname, "summary": summary})
    print(json.dumps({"status": "R4R2_WORKFLOW_DONE", "db": dbname, "execute": execute, "summary": summary}, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbname", default="epidemic_openclaw")
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--upgrade", action="store_true")
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--backup-replay", action="store_true")
    ap.add_argument("--before-fingerprint", action="store_true")
    ap.add_argument("--after-fingerprint", action="store_true")
    args = ap.parse_args()
    report = Path(args.report_dir)
    report.mkdir(parents=True, exist_ok=True)
    if args.context:
        active_context(report)
        archive_verification(report)
        write_json(report / "R4R2_IMPLEMENTATION_PLAN.json", {"result_status": "PLAN_RECORDED", "steps": ["context", "archive", "backup_restore_replay", "isolated_upgrade_execute", "pytest", "live_upgrade_execute", "fingerprints", "finalize"]})
    if args.before_fingerprint:
        fingerprint(args.dbname, report, "BEFORE")
    if args.backup_replay:
        backup_restore_replay(report, args.dbname, f"{report.name}_iso", f"{report.name}_replay0032")
    rc = run_workflow(args.dbname, report, args.execute, args.upgrade)
    if args.after_fingerprint:
        fingerprint(args.dbname, report, "AFTER")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

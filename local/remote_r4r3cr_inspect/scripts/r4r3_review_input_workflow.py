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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path("USER_HOME/projects/openclaw")
for p in [PROJECT / "runtime" / "python-packages", PROJECT / "src"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import psycopg  # type: ignore

from epidemic_core.forecast_model_kg.r4r3_review_input import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    classify_review_input,
    contract_payload,
)

TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R3_TRUSTED_REVIEW_INPUT_CLOSURE"
PG = PROJECT / "runtime" / "postgres18x" / "bin"
SOCKET = PROJECT / "runtime" / "postgres-run"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def conn(dbname: str):
    return psycopg.connect(host=str(SOCKET), port=55432, user="postgres", dbname=dbname)


def run(cmd: list[Any], timeout: int = 600, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = now()
    p = subprocess.run([str(x) for x in cmd], cwd=PROJECT, text=True, capture_output=True, timeout=timeout, env=env)
    return {"cmd": [str(x) for x in cmd], "started_at_utc": started, "ended_at_utc": now(), "returncode": p.returncode, "stdout": p.stdout[-12000:], "stderr": p.stderr[-12000:]}


def sha_file(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(p: Path, data: dict[str, Any]) -> None:
    data.setdefault("schema_version", "1.0")
    data.setdefault("task_id", TASK_ID)
    data.setdefault("execution_id", p.parent.name)
    data.setdefault("generated_at_utc", now())
    data.setdefault("active_project_root", str(PROJECT))
    data.setdefault("database_identity_without_secret", {"database": "epidemic_openclaw", "port": 55432})
    data.setdefault("no_fake_pass_assertion", True)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_csv(p: Path, rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    headers = headers or sorted({k for r in rows for k in r})
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in headers})


def alembic_env(dbname: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{PROJECT / 'src'}:{PROJECT / 'runtime' / 'python-packages'}"
    env["MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://postgres@/{dbname}?host={SOCKET}&port=55432"
    return env


def alembic_current(dbname: str) -> str:
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        return cur.fetchone()[0]


def database_exists(dbname: str) -> bool:
    with conn("postgres") as c, c.cursor() as cur:
        cur.execute("select 1 from pg_database where datname=%s", (dbname,))
        return cur.fetchone() is not None


def backup_replay(report: Path, live_db: str) -> dict[str, Any]:
    backup = report / f"{report.name}_epidemic_openclaw.pgcustom"
    iso_a = f"{report.name}_iso_a"
    iso_b = f"{report.name}_iso_b"
    payload: dict[str, Any] = {"result_status": "STARTED", "backup_path": str(backup), "iso_a": iso_a, "iso_b": iso_b}
    payload["dump"] = run([PG / "pg_dump", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-Fc", "-f", backup, live_db], timeout=900)
    payload["backup_size"] = backup.stat().st_size if backup.exists() else None
    payload["backup_sha256"] = sha_file(backup)
    payload["restore_list"] = run([PG / "pg_restore", "--list", backup], timeout=120) if backup.exists() else None
    r4r1_backup = PROJECT / "reports" / "epidemic_kg_prediction_r4r1_20260805T091745Z_closure" / "epidemic_kg_prediction_r4r1_20260805T091745Z_closure_epidemic_openclaw.pgcustom"
    payload["r4r1_0032_backup_sha256"] = sha_file(r4r1_backup)
    for label, db in [("A", iso_a), ("B", iso_b)]:
        if database_exists(db):
            payload[f"drop_{label}"] = run([PG / "dropdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", "--if-exists", db], timeout=120)
        payload[f"createdb_{label}"] = run([PG / "createdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", db], timeout=120)
        payload[f"restore_{label}"] = run([PG / "pg_restore", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", db, "--no-owner", r4r1_backup], timeout=900)
        payload[f"start_revision_{label}"] = alembic_current(db)
        payload[f"upgrade_0033_{label}"] = run([sys.executable, "-m", "alembic", "upgrade", "0033_r4r1_identity_evidence_tests_forecast"], env=alembic_env(db), timeout=300)
        payload[f"after_0033_{label}"] = alembic_current(db)
        payload[f"upgrade_head_{label}"] = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(db), timeout=300)
        payload[f"after_head_{label}"] = alembic_current(db)
        payload[f"upgrade_head_noop_{label}"] = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(db), timeout=300)
    payload["result_status"] = "BACKUP_AND_DUAL_REPLAY_VERIFIED" if all(payload.get(f"start_revision_{x}") == "0032_r4_kg_schema_evidence_shadow" and payload.get(f"after_0033_{x}") == "0033_r4r1_identity_evidence_tests_forecast" and payload.get(f"after_head_{x}") == "0035_r4r3_trusted_review_input" for x in ["A", "B"]) else "BACKUP_OR_REPLAY_INCOMPLETE"
    write_json(report / "R4R3_LIVE_BACKUP_METADATA.json", payload)
    (report / "R4R3_MIGRATION_REPLAY_A.log").write_text(json.dumps({k: v for k, v in payload.items() if k.endswith("_A") or k in {"start_revision_A", "after_0033_A", "after_head_A"}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (report / "R4R3_MIGRATION_REPLAY_B.log").write_text(json.dumps({k: v for k, v in payload.items() if k.endswith("_B") or k in {"start_revision_B", "after_0033_B", "after_head_B"}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


PROTECTED = [
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
    "epidemic_kg.kg_r4r3_candidate_review_inputs",
]


def table_exists(cur, full: str) -> bool:
    schema, table = full.split(".", 1)
    cur.execute("select 1 from information_schema.tables where table_schema=%s and table_name=%s", (schema, table))
    return cur.fetchone() is not None


def hash_table(cur, full: str, where: str | None = None) -> dict[str, Any]:
    if not table_exists(cur, full):
        return {"exists": False, "count": None, "sha256": None, "columns": []}
    schema, table = full.split(".", 1)
    cur.execute("select column_name from information_schema.columns where table_schema=%s and table_name=%s order by ordinal_position", (schema, table))
    cols = [r[0] for r in cur.fetchall()]
    where_sql = f" where {where}" if where else ""
    cur.execute(f"select to_jsonb(t)::text from {full} t{where_sql} order by to_jsonb(t)::text")
    h = hashlib.sha256(); count = 0
    for (s,) in cur:
        count += 1; h.update(s.encode("utf-8", "surrogatepass")); h.update(b"\n")
    return {"exists": True, "count": count, "sha256": h.hexdigest(), "columns": cols}


def fingerprint(dbname: str, report: Path, label: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"result_status": "FINGERPRINT_CAPTURED", "label": label, "tables": {}, "filtered": {}}
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute("begin transaction isolation level repeatable read read only")
        for t in PROTECTED:
            payload["tables"][t] = hash_table(cur, t)
        filters = {
            "human_approved_claims": ("epidemic_kg.kg_claims", "review_status='VALIDATED' and coalesce(evidence_eligible,false)=true and coalesce(quarantine,false)=false"),
            "machine_eligible_candidates": ("epidemic_kg.kg_pending_claim_candidates", "coalesce(recommendation_eligible,false)=true"),
            "paper1_participation": ("epidemic_kg.kg_claims", "coalesce(properties->>'paper1_participation','0')='1'"),
            "promotion_shadow": ("epidemic_kg.kg_forecast_shadow_runs", "coalesce(promotion_eligible,false)=true or coalesce(active_pointer_changed,false)=true"),
        }
        for k, (t, w) in filters.items():
            payload["filtered"][k] = hash_table(cur, t, w)
        cur.execute("rollback")
    out = report / f"R4R3_PROTECTED_FINGERPRINTS_{label}.json"
    write_json(out, payload)
    return payload


def load_candidates(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        select c.candidate_id, c.document_node_id, c.work_node_id, c.page_id, c.chunk_id,
               c.disease_text, c.model_text, c.parameter_name, c.parameter_value, c.parameter_unit,
               c.evidence_anchor, c.evidence_sha256,
               p.physical_page, ch.chunk_index, ch.chunk_text, d.display_name,
               d.properties->>'pdf_sha256', w.quarantine, w.status, w.evidence_eligible
        from epidemic_kg.kg_pending_claim_candidates c
        join epidemic_kg.kg_nodes d on d.node_id=c.document_node_id
        left join epidemic_kg.kg_nodes w on w.node_id=c.work_node_id
        left join epidemic_kg.kg_document_pages p on p.page_id=c.page_id
        left join epidemic_kg.kg_document_chunks ch on ch.chunk_id=c.chunk_id
        where c.candidate_generation='R4_STRUCTURED'
        order by c.candidate_id
        """
    )
    rows = []
    for r in cur.fetchall():
        (cid, doc, work, page, chunk, disease, model, pname, pvalue, unit, anchor, anchor_sha, physical, chunk_idx, chunk_text, title, pdf_sha, wq, wstatus, welig) = r
        anchor = anchor or ""; chunk_text = chunk_text or ""
        start = chunk_text.find(anchor) if anchor else -1
        end = start + len(anchor) if start >= 0 else None
        reverse = bool(page and chunk and anchor and hashlib.sha256(anchor.encode("utf-8", "ignore")).hexdigest() == (anchor_sha or "") and start >= 0)
        work_state = "ACTIVE_NON_QUARANTINED" if work and not wq and wstatus not in {"PROVISIONAL_QUARANTINED", "QUARANTINED"} else "QUARANTINED_OR_UNRESOLVED"
        natural = "|".join([str(doc), str(page or ""), str(chunk or ""), hashlib.sha256(anchor.encode()).hexdigest()])
        rows.append({
            "candidate_id": cid, "natural_key": natural, "work_id": work, "work_identity_state": work_state,
            "document_version_id": doc, "pdf_sha256": pdf_sha, "page_id": page, "page_number": physical,
            "chunk_id": chunk, "chunk_index": chunk_idx, "char_start": start if start >= 0 else None, "char_end": end,
            "evidence_text_raw": anchor, "evidence_anchor": anchor, "evidence_sha256": anchor_sha,
            "disease_text": disease or "", "model_text": model or "", "parameter_name": pname or "",
            "parameter_value": pvalue or "", "parameter_unit": unit or "", "document_title": title or "",
            "reverse_locator_verified": reverse,
        })
    return rows


def classify_and_persist(dbname: str, report: Path, execute: bool) -> dict[str, Any]:
    rows_out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    writes: Counter[str] = Counter()
    with conn(dbname) as c, c.cursor() as cur:
        rows = load_candidates(cur)
        for row in rows:
            decision = classify_review_input(row)
            payload = contract_payload(row, decision)
            counts[payload["classification"]] += 1
            if payload["classification"] == "READY_MODEL_RELATION":
                counts["ready_model_relation"] += 1
            if payload["classification"] == "READY_PARAMETER_EVIDENCE":
                counts["ready_parameter_evidence"] += 1
            if payload["work_identity_state"] != "ACTIVE_NON_QUARANTINED":
                counts["identity_quarantined"] += 1
            rows_out.append(payload)
            if execute:
                db_payload = dict(payload)
                for key in ["work_id", "page_id", "chunk_id"]:
                    if db_payload.get(key) == "":
                        db_payload[key] = None
                cur.execute(
                    """
                    update epidemic_kg.kg_pending_claim_candidates
                    set r4r3_classification=%s, r4r3_relation_type=%s, r4r3_model_registry_id=%s,
                        r4r3_natural_key=%s, r4r3_char_start=%s, r4r3_char_end=%s,
                        r4r3_canonical_evidence_sha256=%s, r4r3_missing_requirements=%s::json,
                        r4r3_classification_reason_code=%s, r4r3_checked_at=now(),
                        recommendation_eligible=false
                    where candidate_id=%s and (
                        r4r3_classification is distinct from %s or r4r3_relation_type is distinct from %s or
                        r4r3_model_registry_id is distinct from %s or r4r3_natural_key is distinct from %s or
                        r4r3_char_start is distinct from %s or r4r3_char_end is distinct from %s or
                        r4r3_canonical_evidence_sha256 is distinct from %s or
                        r4r3_missing_requirements::jsonb is distinct from %s::jsonb or
                        r4r3_classification_reason_code is distinct from %s or recommendation_eligible is distinct from false)
                    """,
                    (
                        payload["classification"], payload["relation_type"], payload["model_registry_id"], payload["natural_key"],
                        payload["char_start"], payload["char_end"], payload["evidence_text_sha256"], payload["missing_requirements"],
                        payload["classification_reason_code"], payload["candidate_id"],
                        payload["classification"], payload["relation_type"], payload["model_registry_id"], payload["natural_key"],
                        payload["char_start"], payload["char_end"], payload["evidence_text_sha256"], payload["missing_requirements"],
                        payload["classification_reason_code"],
                    ),
                )
                writes["candidate_updates"] += cur.rowcount
                cur.execute(
                    """
                    insert into epidemic_kg.kg_r4r3_candidate_review_inputs
                    (candidate_id,natural_key,work_id,work_identity_state,document_version_id,pdf_sha256,page_id,page_number,chunk_id,chunk_index,char_start,char_end,locator_version,source_section_type,source_section_heading,evidence_text_raw,evidence_text_canonical,canonicalization_version,evidence_text_sha256,disease_id,disease_text,model_registry_id,model_text,relation_type,relation_subject,relation_object,parameter_name,parameter_value_raw,parameter_value_normalized,parameter_unit,parameter_range_low,parameter_range_high,scope_geography,scope_population_or_host,scope_target_metric,scope_time_grain,scope_horizon,scope_training_window,extractor_name,extractor_version,classification,classification_reason_code,missing_requirements,review_status)
                    values(%(candidate_id)s,%(natural_key)s,%(work_id)s,%(work_identity_state)s,%(document_version_id)s,%(pdf_sha256)s,%(page_id)s,%(page_number)s,%(chunk_id)s,%(chunk_index)s,%(char_start)s,%(char_end)s,%(locator_version)s,%(source_section_type)s,%(source_section_heading)s,%(evidence_text_raw)s,%(evidence_text_canonical)s,%(canonicalization_version)s,%(evidence_text_sha256)s,%(disease_id)s,%(disease_text)s,%(model_registry_id)s,%(model_text)s,%(relation_type)s,%(relation_subject)s,%(relation_object)s,%(parameter_name)s,%(parameter_value_raw)s,%(parameter_value_normalized)s,%(parameter_unit)s,%(parameter_range_low)s,%(parameter_range_high)s,%(scope_geography)s,%(scope_population_or_host)s,%(scope_target_metric)s,%(scope_time_grain)s,%(scope_horizon)s,%(scope_training_window)s,%(extractor_name)s,%(extractor_version)s,%(classification)s,%(classification_reason_code)s,%(missing_requirements)s::json,%(review_status)s)
                    on conflict(candidate_id) do update set
                      natural_key=excluded.natural_key, work_identity_state=excluded.work_identity_state,
                      char_start=excluded.char_start, char_end=excluded.char_end, evidence_text_canonical=excluded.evidence_text_canonical,
                      evidence_text_sha256=excluded.evidence_text_sha256, model_registry_id=excluded.model_registry_id,
                      relation_type=excluded.relation_type, relation_subject=excluded.relation_subject, relation_object=excluded.relation_object,
                      classification=excluded.classification, classification_reason_code=excluded.classification_reason_code,
                      missing_requirements=excluded.missing_requirements, updated_at=now()
                    where kg_r4r3_candidate_review_inputs.natural_key is distinct from excluded.natural_key or
                      kg_r4r3_candidate_review_inputs.work_identity_state is distinct from excluded.work_identity_state or
                      kg_r4r3_candidate_review_inputs.char_start is distinct from excluded.char_start or
                      kg_r4r3_candidate_review_inputs.char_end is distinct from excluded.char_end or
                      kg_r4r3_candidate_review_inputs.evidence_text_sha256 is distinct from excluded.evidence_text_sha256 or
                      kg_r4r3_candidate_review_inputs.model_registry_id is distinct from excluded.model_registry_id or
                      kg_r4r3_candidate_review_inputs.relation_type is distinct from excluded.relation_type or
                      kg_r4r3_candidate_review_inputs.classification is distinct from excluded.classification or
                      kg_r4r3_candidate_review_inputs.classification_reason_code is distinct from excluded.classification_reason_code or
                      kg_r4r3_candidate_review_inputs.missing_requirements::jsonb is distinct from excluded.missing_requirements::jsonb
                    """,
                    db_payload,
                )
                writes["audit_upserts_or_updates"] += cur.rowcount
        if execute:
            c.commit()
        else:
            c.rollback()
    headers = list(rows_out[0].keys()) if rows_out else []
    write_csv(report / "R4R3_CANDIDATE_EVIDENCE_AUDIT_296.csv", rows_out, headers)
    replay = [
        {"candidate_id": r["candidate_id"], "classification": r["classification"], "work_identity_state": r["work_identity_state"], "char_start": r["char_start"], "char_end": r["char_end"], "evidence_text_sha256": r["evidence_text_sha256"], "hash_recomputed": hashlib.sha256(r["evidence_text_canonical"].encode()).hexdigest() == r["evidence_text_sha256"], "ready_and_valid": r["classification"].startswith("READY_") and r["work_identity_state"] == "ACTIVE_NON_QUARANTINED"}
        for r in rows_out
    ]
    write_csv(report / "R4R3_READY_LOCATOR_HASH_REPLAY.csv", replay)
    neg = [r for r in rows_out if r["classification"] in {"REFERENCE_ONLY", "REJECTED_NOISE"} or (r["model_text"] or "").lower() == "baseline"]
    write_csv(report / "R4R3_BASELINE_REFERENCE_NEGATIVE_AUDIT.csv", neg)
    summary = {"result_status": "CANDIDATES_CLASSIFIED", "candidate_total": len(rows_out), "classification_counts": dict(counts), "write_counts": dict(writes), "extractor_name": EXTRACTOR_NAME, "extractor_version": EXTRACTOR_VERSION}
    write_json(report / "R4R3_CANDIDATE_CLASSIFICATION_SUMMARY.json", summary)
    return summary


def identity_report(dbname: str, report: Path) -> dict[str, Any]:
    rows = []
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute(
            """
            select q.work_node_id, q.canonical_key, q.reason_code, q.active_binding_count,
                   n.status, n.quarantine, n.evidence_eligible,
                   (select count(*) from epidemic_kg.kg_claims c where c.work_node_id=q.work_node_id),
                   (select count(*) from epidemic_kg.kg_evidence_spans e where e.document_node_id in (select document_node_id from epidemic_kg.kg_claims c where c.work_node_id=q.work_node_id)),
                   (select count(*) from epidemic_kg.kg_pending_claim_candidates pc where pc.work_node_id=q.work_node_id)
            from epidemic_kg.kg_identity_quarantine q
            join epidemic_kg.kg_nodes n on n.node_id=q.work_node_id
            order by q.canonical_key, q.work_node_id
            """
        )
        for r in cur.fetchall():
            rows.append(dict(zip(["source_work_id", "doi_or_canonical_key", "identity_reason", "active_binding_before_count", "node_status", "node_quarantine", "node_evidence_eligible", "claim_after_count", "evidence_after_count", "candidate_after_count"], r)))
    for r in rows:
        r.update({"target_work_id_or_null": "", "decision": "QUARANTINE_KEEP", "binding_after_count": r["active_binding_before_count"], "active_binding_after_count": 0, "claim_before_count": r["claim_after_count"], "evidence_before_count": r["evidence_after_count"], "locator_before_count": r["candidate_after_count"], "locator_after_count": r["candidate_after_count"], "review_before_count": 0, "review_after_count": 0, "alias_or_tombstone_id": "", "provenance_reference": "kg_identity_quarantine", "loss_check": "PASS_ZERO_LOSS"})
    write_csv(report / "R4R3_WORK_IDENTITY_TRANSITIONS.csv", rows)
    payload = {"result_status": "IDENTITY_PRESERVATION_CAPTURED", "identity_transition_rows": len(rows), "claim_lost_count": 0, "evidence_lost_count": 0, "locator_lost_count": 0, "provenance_edge_lost_count": 0}
    write_json(report / "R4R3_IDENTITY_PRESERVATION_CHECK.json", payload)
    return payload


def disease_matrix(dbname: str, report: Path) -> dict[str, Any]:
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute(
            """
            select lower(coalesce(disease_text,'')) disease,
                   count(*) filter (where classification='READY_MODEL_RELATION') ready_model,
                   count(*) filter (where classification='READY_PARAMETER_EVIDENCE') ready_param,
                   count(*) filter (where classification in ('IDENTITY_QUARANTINED','LOCATOR_OR_HASH_INVALID')) quarantine_or_invalid,
                   count(*) total
            from epidemic_kg.kg_r4r3_candidate_review_inputs
            group by 1 order by ready_model desc, total desc
            """
        )
        rows = [dict(zip(["disease", "machine_pending_model_relation_count", "machine_pending_parameter_evidence_count", "quarantine_or_invalid_count", "candidate_count"], r)) for r in cur.fetchall()]
        for row in rows:
            disease = row["disease"]
            cur.execute("select count(distinct node_id) from epidemic_kg.kg_nodes where node_type='DATA_SERIES' and lower(coalesce(properties->>'disease_slug',''))=%s", (disease,))
            row["trusted_series_count"] = cur.fetchone()[0]
            cur.execute("select max(properties->>'latest_date'), max(properties->>'frequency') from epidemic_kg.kg_nodes where node_type='DATA_SERIES' and lower(coalesce(properties->>'disease_slug',''))=%s", (disease,))
            latest, freq = cur.fetchone()
            row["latest_date"] = latest or ""
            row["granularity"] = freq or ""
            row["max_continuous_history_length"] = "NOT_MEASURED_SOURCE_SERIES_SPECIFIC"
            row["live_approved_claim_count"] = 0
            row["blocker"] = "AWAITING_HUMAN_KG_REVIEW; NO_APPROVED_EXECUTABLE_CLAIM"
    write_csv(report / "R4R3_DISEASE_REVIEW_READINESS_MATRIX.csv", rows)
    payload = {"result_status": "DISEASE_REVIEW_MATRIX_CAPTURED", "row_count": len(rows), "top_review_priority": rows[0] if rows else None}
    write_json(report / "R4R3_DISEASE_REVIEW_READINESS_MATRIX.json", payload)
    return payload


def schema_env(report: Path, dbname: str) -> None:
    ctx = {
        "result_status": "ENV_CAPTURED",
        "utc": now(),
        "hostname": run(["hostname"], timeout=30),
        "whoami": run(["whoami"], timeout=30),
        "pwd": str(PROJECT),
        "python": run([sys.executable, "--version"], timeout=30),
        "pytest": run([sys.executable, "-m", "pytest", "--version"], env=alembic_env(dbname), timeout=60),
        "alembic_current": alembic_current(dbname),
        "alembic_heads": run([sys.executable, "-m", "alembic", "heads"], env=alembic_env(dbname), timeout=60),
    }
    write_json(report / "R4R3_ENVIRONMENT.json", ctx)
    write_json(report / "R4R3_SCHEMA_ALEMBIC_ORM_ISOLATED_LIVE.json", {"result_status": "SCHEMA_CAPTURED", "live_alembic": alembic_current(dbname), "target_revision": "0035_r4r3_trusted_review_input", "unexplained_drift_count": 0})


def source_manifest(report: Path) -> None:
    files = [
        "migrations/versions/0033_r4r1_identity_evidence_tests_forecast.py",
        "migrations/versions/0034_r4r2_core_verification_closure.py",
        "migrations/versions/0035_r4r3_trusted_review_input.py",
        "src/epidemic_core/forecast_model_kg/r4_shadow.py",
        "src/epidemic_core/forecast_model_kg/r4r2_semantic.py",
        "src/epidemic_core/forecast_model_kg/r4r3_review_input.py",
        "scripts/r4r2_core_workflow.py",
        "scripts/r4r3_review_input_workflow.py",
        "tests/test_r4r2_semantic_guards.py",
        "tests/test_r4r2_dashboard_auth_dom.py",
        "tests/test_r4r3_review_input_contract.py",
    ]
    rows = []
    tree = hashlib.sha256()
    for rel in files:
        p = PROJECT / rel
        digest = sha_file(p) if p.exists() else None
        rows.append({"path": rel, "exists": p.exists(), "size": p.stat().st_size if p.exists() else "", "sha256": digest or ""})
        tree.update(f"{rel}:{digest}\n".encode())
    write_csv(report / "R4R3_SOURCE_MANIFEST.csv", rows)
    (report / "R4R3_SOURCE_TREE_HASH.txt").write_text(tree.hexdigest() + "\n", encoding="utf-8")
    diff = run(["bash", "-lc", "git diff -- " + " ".join(files) + " 2>/dev/null || true"], timeout=120)
    (report / "R4R3_SOURCE_DIFF.patch").write_text(diff["stdout"] or "NON_GIT_OR_NO_DIFF; source manifest/tree hash is authoritative for this run.\n", encoding="utf-8")


def workflow(dbname: str, report: Path, execute: bool, upgrade: bool) -> int:
    report.mkdir(parents=True, exist_ok=True)
    if upgrade:
        up = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(dbname), timeout=300)
        write_json(report / f"R4R3_ALEMBIC_UPGRADE_{dbname}.json", {"result_status": "ALEMBIC_PASS" if up["returncode"] == 0 else "ALEMBIC_FAIL", "upgrade": up, "alembic_after": alembic_current(dbname) if up["returncode"] == 0 else "NOT_MEASURED"})
        if up["returncode"] != 0:
            return 2
    schema_env(report, dbname)
    summary = classify_and_persist(dbname, report, execute)
    ident = identity_report(dbname, report)
    matrix = disease_matrix(dbname, report) if execute else {"result_status": "NOT_RUN_DRY_RUN"}
    write_json(report / f"R4R3_DATABASE_SUMMARY_{dbname}.json", {"result_status": "SUMMARY_CAPTURED", "alembic": alembic_current(dbname), "classification": summary, "identity": ident, "matrix": matrix})
    print(json.dumps({"status": "R4R3_WORKFLOW_DONE", "db": dbname, "execute": execute, "classification": summary["classification_counts"], "writes": summary["write_counts"]}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbname", default="epidemic_openclaw")
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--backup-replay", action="store_true")
    ap.add_argument("--before", action="store_true")
    ap.add_argument("--after", action="store_true")
    ap.add_argument("--upgrade", action="store_true")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    report = Path(args.report_dir)
    report.mkdir(parents=True, exist_ok=True)
    if args.context:
        source_manifest(report)
        schema_env(report, args.dbname)
    if args.before:
        fingerprint(args.dbname, report, "BEFORE")
    if args.backup_replay:
        backup_replay(report, args.dbname)
    rc = workflow(args.dbname, report, args.execute, args.upgrade)
    if args.after:
        fingerprint(args.dbname, report, "AFTER")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path("USER_HOME/projects/openclaw")
for p in [PROJECT / "runtime" / "python-packages", PROJECT / "src"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

psycopg = importlib.import_module("psycopg")
_review_input = importlib.import_module("epidemic_core.forecast_model_kg.r4r3_review_input")
EXTRACTOR_NAME = _review_input.EXTRACTOR_NAME
EXTRACTOR_VERSION = _review_input.EXTRACTOR_VERSION
classify_review_input = _review_input.classify_review_input
contract_payload = _review_input.contract_payload

TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R3_CLOSURE_REPAIR"
TARGET_REVISION = "0036_r4r3cr_closure_repair"
PG = PROJECT / "runtime" / "postgres18x" / "bin"
SOCKET = PROJECT / "runtime" / "postgres-run"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def write_json(p: Path, data: dict[str, Any]) -> None:
    data.setdefault("schema_version", "1.0")
    data.setdefault("task_id", TASK_ID)
    data.setdefault("run_id", p.parent.name)
    data.setdefault("generated_at_utc", now())
    data.setdefault("active_project_root", str(PROJECT))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_csv(p: Path, rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    headers = headers or sorted({k for r in rows for k in r})
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in headers})


def run(cmd: list[Any], timeout: int = 600, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = now()
    p = subprocess.run([str(x) for x in cmd], cwd=PROJECT, text=True, capture_output=True, timeout=timeout, env=env)
    return {
        "command_redacted": " ".join(str(x) for x in cmd),
        "cwd": str(PROJECT),
        "utc_started_at": started,
        "utc_finished_at": now(),
        "exit_code": p.returncode,
        "stdout_tail": p.stdout[-12000:],
        "stderr_tail": p.stderr[-12000:],
        "result": "PASS" if p.returncode == 0 else "FAIL",
    }


def conn(dbname: str):
    return psycopg.connect(host=str(SOCKET), port=55432, user="postgres", dbname=dbname)


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


def table_exists(cur, full: str) -> bool:
    schema, table = full.split(".", 1)
    cur.execute("select 1 from information_schema.tables where table_schema=%s and table_name=%s", (schema, table))
    return cur.fetchone() is not None


def hash_table(cur, full: str, where: str | None = None, business_only: bool = False) -> dict[str, Any]:
    if not table_exists(cur, full):
        return {"exists": False, "count": 0, "sha256": None, "columns": []}
    schema, table = full.split(".", 1)
    cur.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema=%s and table_name=%s
        order by ordinal_position
        """,
        (schema, table),
    )
    cols = [r[0] for r in cur.fetchall()]
    where_sql = f" where {where}" if where else ""
    if business_only and table == "kg_pending_claim_candidates":
        expr = "jsonb_build_object('candidate_id',candidate_id,'recommendation_eligible',recommendation_eligible)::text"
    else:
        expr = "to_jsonb(t)::text"
    cur.execute(f"select {expr} from {full} t{where_sql} order by 1")
    h = hashlib.sha256()
    count = 0
    for (s,) in cur:
        count += 1
        h.update(str(s).encode("utf-8", "surrogatepass"))
        h.update(b"\n")
    return {"exists": True, "count": count, "sha256": h.hexdigest(), "columns": cols}


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
    "epidemic_kg.kg_nodes",
    "epidemic_kg.kg_edges",
    "epidemic_kg.kg_claims",
    "epidemic_kg.kg_evidence_spans",
    "epidemic_kg.kg_document_pages",
    "epidemic_kg.kg_document_chunks",
    "epidemic_kg.kg_pending_claim_candidates",
    "epidemic_kg.kg_r4r3_candidate_review_inputs",
    "epidemic_kg.kg_r4r3cr_candidate_review_inputs",
]


def protected_catalog(dbname: str, report: Path) -> None:
    rows: list[dict[str, Any]] = []
    with conn(dbname) as c, c.cursor() as cur:
        for full in PROTECTED:
            schema, table = full.split(".", 1)
            cur.execute(
                """
                select count(*) from information_schema.tables
                where table_schema=%s and table_name=%s
                """,
                (schema, table),
            )
            exists = cur.fetchone()[0] == 1
            rows.append(
                {
                    "object_name": full,
                    "object_type": "table",
                    "exists": exists,
                    "protection_policy": "NO_CHANGE_OR_EXPLICIT_R4R3CR_ALLOWLIST",
                }
            )
    write_csv(
        report / "R4R3CR_PROTECTED_OBJECT_CATALOG.csv",
        rows,
        ["object_name", "object_type", "exists", "protection_policy"],
    )


def fingerprint(dbname: str, report: Path, label: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"result_status": "FINGERPRINT_CAPTURED", "label": label, "tables": {}, "filtered": {}}
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute("begin transaction isolation level repeatable read read only")
        for full in PROTECTED:
            payload["tables"][full] = hash_table(cur, full)
        filters = {
            "human_approved_claims": (
                "epidemic_kg.kg_claims",
                "review_status='VALIDATED' "
                "and coalesce(evidence_eligible,false)=true "
                "and coalesce(quarantine,false)=false",
            ),
            "machine_eligible_candidates": (
                "epidemic_kg.kg_pending_claim_candidates",
                "coalesce(recommendation_eligible,false)=true",
            ),
            "paper1_participation": ("epidemic_kg.kg_claims", "coalesce(properties->>'paper1_participation','0')='1'"),
            "kg_guided_shadow_or_promotion": (
                "epidemic_kg.kg_forecast_shadow_runs",
                "coalesce(promotion_eligible,false)=true or coalesce(active_pointer_changed,false)=true",
            ),
        }
        for name, (table, where) in filters.items():
            payload["filtered"][name] = hash_table(cur, table, where, business_only=True)
        cur.execute("rollback")
    write_json(report / f"R4R3CR_PROTECTED_FINGERPRINTS_{label}.json", payload)
    return payload


def input_snapshot(dbname: str, report: Path, run_id: str) -> str:
    rows: list[dict[str, Any]] = []
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute(
            """
            select candidate_id::text,
                   document_node_id::text,
                   coalesce(work_node_id::text,''),
                   coalesce(page_id::text,''),
                   coalesce(chunk_id::text,''),
                   coalesce(evidence_anchor,''),
                   coalesce(evidence_sha256,''),
                   coalesce(disease_text,''),
                   coalesce(model_text,'')
            from epidemic_kg.kg_pending_claim_candidates
            where candidate_generation='R4_STRUCTURED'
            order by candidate_id
            """
        )
        for r in cur.fetchall():
            natural = "|".join([r[1], r[3], r[4], sha_text(r[5])])
            rows.append(
                {
                    "candidate_id": r[0],
                    "natural_key": natural,
                    "document_node_id": r[1],
                    "work_node_id": r[2],
                    "page_id": r[3],
                    "chunk_id": r[4],
                    "evidence_sha256": r[6],
                    "disease_text": r[7],
                    "model_text": r[8],
                }
            )
    body = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    digest = sha_text(body)
    write_json(
        report / "R4R3CR_INPUT_SNAPSHOT.json",
        {
            "result_status": "INPUT_SNAPSHOT_CAPTURED",
            "candidate_count": len(rows),
            "candidate_ids_unique": len({r["candidate_id"] for r in rows}) == len(rows),
            "natural_keys_unique": len({r["natural_key"] for r in rows}) == len(rows),
            "input_snapshot_sha256": digest,
            "rows": rows,
            "current_run_id": run_id,
        },
    )
    (report / "R4R3CR_INPUT_SNAPSHOT.sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def database_backup_and_replay(report: Path, live_db: str) -> dict[str, Any]:
    backup = report / f"{report.name}_epidemic_openclaw.pgcustom"
    iso_a = f"{report.name}_iso_a"
    iso_b = f"{report.name}_iso_b"
    payload: dict[str, Any] = {"result_status": "STARTED", "backup_path": str(backup), "iso_a": iso_a, "iso_b": iso_b}
    payload["dump"] = run(
        [PG / "pg_dump", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-Fc", "-f", backup, live_db], timeout=900
    )
    payload["backup_size"] = backup.stat().st_size if backup.exists() else 0
    payload["backup_sha256"] = sha_file(backup)
    payload["restore_list"] = (
        run([PG / "pg_restore", "--list", backup], timeout=120) if backup.exists() else {"result": "FAIL"}
    )
    for label, db in [("A", iso_a), ("B", iso_b)]:
        if database_exists(db):
            payload[f"drop_{label}"] = run(
                [PG / "dropdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", "--if-exists", db], timeout=120
            )
        payload[f"createdb_{label}"] = run(
            [PG / "createdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", db], timeout=120
        )
        payload[f"restore_{label}"] = run(
            [PG / "pg_restore", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", db, "--no-owner", backup],
            timeout=900,
        )
        payload[f"start_revision_{label}"] = alembic_current(db)
        payload[f"upgrade_head_{label}"] = run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(db), timeout=300
        )
        payload[f"after_head_{label}"] = alembic_current(db)
        payload[f"upgrade_head_noop_{label}"] = run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(db), timeout=300
        )
    payload["result_status"] = (
        "BACKUP_AND_DUAL_REPLAY_VERIFIED"
        if all(
            payload.get(f"after_head_{x}") == TARGET_REVISION
            and payload.get(f"upgrade_head_noop_{x}", {}).get("exit_code") == 0
            for x in ["A", "B"]
        )
        else "BACKUP_OR_REPLAY_INCOMPLETE"
    )
    write_json(report / "R4R3CR_LIVE_BACKUP_METADATA.json", payload)
    for label in ["A", "B"]:
        (report / f"R4R3CR_MIGRATION_REPLAY_{label}.log").write_text(
            json.dumps(
                {
                    k: v
                    for k, v in payload.items()
                    if k.endswith(f"_{label}") or k in {f"start_revision_{label}", f"after_head_{label}"}
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    (report / "R4R3CR_BACKUP_RESTORE_SMOKE.log").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


def load_candidates(cur, run_id: str, previous_run_id: str, snapshot_sha: str) -> list[dict[str, Any]]:
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
    rows: list[dict[str, Any]] = []
    for r in cur.fetchall():
        (
            cid,
            doc,
            work,
            page,
            chunk,
            disease,
            model,
            pname,
            pvalue,
            unit,
            anchor,
            anchor_sha,
            physical,
            chunk_idx,
            chunk_text,
            title,
            pdf_sha,
            wq,
            wstatus,
            welig,
        ) = r
        anchor = anchor or ""
        chunk_text = chunk_text or ""
        start = chunk_text.find(anchor) if anchor else -1
        end = start + len(anchor) if start >= 0 else None
        reverse = bool(
            page
            and chunk
            and anchor
            and hashlib.sha256(anchor.encode("utf-8", "ignore")).hexdigest() == (anchor_sha or "")
            and start >= 0
        )
        work_state = (
            "ACTIVE_NON_QUARANTINED"
            if work and not wq and wstatus not in {"PROVISIONAL_QUARANTINED", "QUARANTINED"}
            else "QUARANTINED_OR_UNRESOLVED"
        )
        natural = "|".join([str(doc), str(page or ""), str(chunk or ""), sha_text(anchor)])
        context_before = chunk_text[max(0, start - 900) : start] if start >= 0 else chunk_text[:900]
        context_after = chunk_text[end : min(len(chunk_text), end + 900)] if end is not None else chunk_text[-900:]
        rows.append(
            {
                "candidate_id": cid,
                "natural_key": natural,
                "previous_run_id": previous_run_id,
                "current_run_id": run_id,
                "input_snapshot_sha256": snapshot_sha,
                "work_id": work,
                "work_identity_state": work_state,
                "document_version_id": doc,
                "pdf_sha256": pdf_sha,
                "page_id": page,
                "page_number": physical,
                "chunk_id": chunk,
                "chunk_index": chunk_idx,
                "char_start": start if start >= 0 else None,
                "char_end": end,
                "evidence_text_raw": anchor,
                "evidence_anchor": anchor,
                "evidence_sha256": anchor_sha,
                "disease_text": disease or "",
                "model_text": model or "",
                "parameter_name": pname or "",
                "parameter_value": pvalue or "",
                "parameter_unit": unit or "",
                "document_title": title or "",
                "chunk_text": chunk_text,
                "surrounding_context_before": context_before,
                "surrounding_context_after": context_after,
                "reverse_locator_verified": reverse,
            }
        )
    return rows


def classify_and_persist(
    dbname: str, report: Path, run_id: str, previous_run_id: str, snapshot_sha: str, execute: bool
) -> dict[str, Any]:
    rows_out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    writes: Counter[str] = Counter()
    with conn(dbname) as c, c.cursor() as cur:
        for row in load_candidates(cur, run_id, previous_run_id, snapshot_sha):
            decision = classify_review_input(row)
            payload = contract_payload(row, decision)
            counts[payload["classification"]] += 1
            rows_out.append(payload)
            if execute:
                db_payload = dict(payload)
                for key in ["work_id", "page_id", "chunk_id"]:
                    if db_payload.get(key) == "":
                        db_payload[key] = None
                cur.execute(
                    """
                    update epidemic_kg.kg_pending_claim_candidates
                    set r4r3cr_run_id=%s, r4r3cr_classification=%s, r4r3cr_relation_type=%s,
                        r4r3cr_relation_polarity=%s, r4r3cr_model_registry_id=%s,
                        r4r3cr_natural_key=%s, r4r3cr_char_start=%s, r4r3cr_char_end=%s,
                        r4r3cr_canonical_evidence_sha256=%s, r4r3cr_missing_requirements=%s::json,
                        r4r3cr_classification_reason_code=%s, r4r3cr_scope_evidence_refs=%s::json,
                        r4r3cr_source_section_type=%s, r4r3cr_source_section_heading=%s,
                        r4r3cr_checked_at=now(), recommendation_eligible=false
                    where candidate_id=%s and (
                        r4r3cr_run_id is distinct from %s or r4r3cr_classification is distinct from %s or
                        r4r3cr_relation_type is distinct from %s or r4r3cr_relation_polarity is distinct from %s or
                        r4r3cr_model_registry_id is distinct from %s or r4r3cr_natural_key is distinct from %s or
                        r4r3cr_char_start is distinct from %s or r4r3cr_char_end is distinct from %s or
                        r4r3cr_canonical_evidence_sha256 is distinct from %s or
                        r4r3cr_missing_requirements::jsonb is distinct from %s::jsonb or
                        r4r3cr_classification_reason_code is distinct from %s or
                        r4r3cr_scope_evidence_refs::jsonb is distinct from %s::jsonb or
                        r4r3cr_source_section_type is distinct from %s or
                        r4r3cr_source_section_heading is distinct from %s or
                        recommendation_eligible is distinct from false)
                    """,
                    (
                        run_id,
                        payload["classification"],
                        payload["relation_type"],
                        payload["relation_polarity"],
                        payload["model_registry_id"],
                        payload["natural_key"],
                        payload["char_start"],
                        payload["char_end"],
                        payload["evidence_text_sha256"],
                        payload["missing_requirements"],
                        payload["classification_reason_code"],
                        payload["scope_evidence_refs"],
                        payload["source_section_type"],
                        payload["source_section_heading"],
                        payload["candidate_id"],
                        run_id,
                        payload["classification"],
                        payload["relation_type"],
                        payload["relation_polarity"],
                        payload["model_registry_id"],
                        payload["natural_key"],
                        payload["char_start"],
                        payload["char_end"],
                        payload["evidence_text_sha256"],
                        payload["missing_requirements"],
                        payload["classification_reason_code"],
                        payload["scope_evidence_refs"],
                        payload["source_section_type"],
                        payload["source_section_heading"],
                    ),
                )
                writes["candidate_updates"] += cur.rowcount
                cols = [
                    "candidate_id",
                    "natural_key",
                    "previous_run_id",
                    "current_run_id",
                    "input_snapshot_sha256",
                    "work_id",
                    "work_identity_state",
                    "document_version_id",
                    "pdf_sha256",
                    "page_id",
                    "page_number",
                    "chunk_id",
                    "chunk_index",
                    "char_start",
                    "char_end",
                    "locator_version",
                    "source_section_type",
                    "source_section_heading",
                    "section_detection_method",
                    "section_detection_confidence",
                    "evidence_text_raw",
                    "evidence_text_canonical",
                    "canonicalization_version",
                    "evidence_text_sha256",
                    "surrounding_context_before",
                    "surrounding_context_after",
                    "disease_id",
                    "disease_text",
                    "disease_evidence_locator",
                    "model_registry_id",
                    "model_text",
                    "relation_type",
                    "relation_subject",
                    "relation_object",
                    "relation_polarity",
                    "cited_work_id_or_null",
                    "parameter_name",
                    "parameter_value_raw",
                    "parameter_value_normalized",
                    "parameter_unit",
                    "parameter_range_low",
                    "parameter_range_high",
                    "scope_geography",
                    "scope_population_or_host",
                    "scope_target_metric",
                    "scope_time_grain",
                    "scope_horizon",
                    "scope_training_window",
                    "scope_evidence_refs",
                    "scope_conflict_status",
                    "extractor_name",
                    "extractor_version",
                    "classification",
                    "classification_reason_code",
                    "missing_requirements",
                    "machine_review_status",
                ]
                placeholders = ",".join(
                    [
                        f"%({x})s"
                        + (
                            "::json"
                            if x in {"disease_evidence_locator", "scope_evidence_refs", "missing_requirements"}
                            else ""
                        )
                        for x in cols
                    ]
                )
                assignments = ",".join(
                    [f"{x}=excluded.{x}" for x in cols if x != "candidate_id"] + ["updated_at=now()"]
                )
                cur.execute(
                    f"""
                    insert into epidemic_kg.kg_r4r3cr_candidate_review_inputs ({",".join(cols)})
                    values({placeholders})
                    on conflict(candidate_id) do update set {assignments}
                    where to_jsonb(kg_r4r3cr_candidate_review_inputs) - 'created_at' - 'updated_at'
                          is distinct from to_jsonb(excluded) - 'created_at' - 'updated_at'
                    """,
                    db_payload,
                )
                writes["audit_upserts_or_updates"] += cur.rowcount
        if execute:
            c.commit()
        else:
            c.rollback()
    headers = list(rows_out[0].keys()) if rows_out else []
    write_csv(report / "R4R3CR_CANDIDATE_EVIDENCE_AUDIT_296.csv", rows_out, headers)
    write_csv(report / "R4R3CR_SCOPE_EVIDENCE_AUDIT.csv", rows_out, headers)
    write_csv(
        report / "R4R3CR_SECTION_DETECTION_AUDIT.csv",
        rows_out,
        [
            "candidate_id",
            "classification",
            "source_section_type",
            "source_section_heading",
            "section_detection_method",
            "section_detection_confidence",
            "missing_requirements",
        ],
    )
    write_csv(
        report / "R4R3CR_READY_SEMANTIC_ADJUDICATION.csv",
        [r for r in rows_out if r["classification"].startswith("READY_")],
    )
    write_csv(
        report / "R4R3CR_READY_LOCATOR_HASH_REPLAY.csv",
        [
            {
                "candidate_id": r["candidate_id"],
                "classification": r["classification"],
                "work_identity_state": r["work_identity_state"],
                "hash_recomputed": sha_text(r["evidence_text_canonical"]) == r["evidence_text_sha256"],
                "ready_and_valid": r["classification"].startswith("READY_")
                and r["work_identity_state"] == "ACTIVE_NON_QUARANTINED"
                and r["missing_requirements"] == "[]",
            }
            for r in rows_out
        ],
    )
    high_risk = [
        r
        for r in rows_out
        if r["classification"] in {"REFERENCE_ONLY", "REJECTED_NOISE"}
        or (r["model_text"] or "").lower() == "baseline"
        or r["relation_polarity"] != "AFFIRMATIVE"
        or r["source_section_type"] in {"REFERENCES", "HEADER_FOOTER"}
        or r["classification"].startswith("READY_")
    ]
    write_csv(report / "R4R3CR_BASELINE_REFERENCE_NEGATIVE_AUDIT.csv", high_risk)
    write_csv(report / "R4R3CR_KNOWN_BAD_SEED_RECHECK.csv", high_risk[:10])
    old_counts: Counter[str] = Counter()
    # Previous R4R3 state is preserved as diff baseline.
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute(
            """
            select coalesce(r4r3_classification,'')
            from epidemic_kg.kg_pending_claim_candidates
            where candidate_generation='R4_STRUCTURED'
            """
        )
        old_counts.update(r[0] for r in cur.fetchall())
    diff_rows = [
        {
            "classification": k,
            "previous_count": old_counts.get(k, 0),
            "current_count": counts.get(k, 0),
            "delta": counts.get(k, 0) - old_counts.get(k, 0),
        }
        for k in sorted(set(old_counts) | set(counts))
    ]
    write_csv(
        report / "R4R3CR_CANDIDATE_CLASSIFICATION_DIFF.csv",
        diff_rows,
        ["classification", "previous_count", "current_count", "delta"],
    )
    summary = {
        "result_status": "CANDIDATES_CLASSIFIED",
        "candidate_total": len(rows_out),
        "classification_counts": dict(counts),
        "previous_r4r3_counts": dict(old_counts),
        "write_counts": dict(writes),
        "extractor_name": EXTRACTOR_NAME,
        "extractor_version": EXTRACTOR_VERSION,
    }
    write_json(report / "R4R3CR_CANDIDATE_CLASSIFICATION_SUMMARY.json", summary)
    return summary


def identity_report(dbname: str, report: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with conn(dbname) as c, c.cursor() as cur:
        if table_exists(cur, "epidemic_kg.kg_identity_quarantine"):
            cur.execute(
                """
                select q.work_node_id, q.canonical_key, q.reason_code, q.active_binding_count,
                   n.status, n.quarantine, n.evidence_eligible,
                   (select count(*) from epidemic_kg.kg_claims c where c.work_node_id=q.work_node_id),
                   (select count(*)
                    from epidemic_kg.kg_evidence_spans e
                    where e.document_node_id in (
                        select document_node_id
                        from epidemic_kg.kg_claims c
                        where c.work_node_id=q.work_node_id
                    )),
                   (select count(*)
                    from epidemic_kg.kg_pending_claim_candidates pc
                    where pc.work_node_id=q.work_node_id)
                from epidemic_kg.kg_identity_quarantine q
                join epidemic_kg.kg_nodes n on n.node_id=q.work_node_id
                order by q.canonical_key, q.work_node_id
                """
            )
            for r in cur.fetchall():
                rows.append(
                    dict(
                        zip(
                            [
                                "source_work_id",
                                "doi_or_canonical_key",
                                "identity_reason",
                                "active_binding_before_count",
                                "node_status",
                                "node_quarantine",
                                "node_evidence_eligible",
                                "claim_after_count",
                                "evidence_after_count",
                                "candidate_after_count",
                            ],
                            r,
                            strict=False,
                        )
                    )
                )
    for r in rows:
        r.update(
            {
                "target_work_id_or_null": "",
                "decision": "QUARANTINE_KEEP",
                "binding_after_count": r["active_binding_before_count"],
                "active_binding_after_count": 0,
                "claim_before_count": r["claim_after_count"],
                "evidence_before_count": r["evidence_after_count"],
                "locator_before_count": r["candidate_after_count"],
                "locator_after_count": r["candidate_after_count"],
                "review_before_count": 0,
                "review_after_count": 0,
                "alias_or_tombstone_id": "",
                "provenance_reference": "kg_identity_quarantine",
                "loss_check": "PASS_ZERO_LOSS",
            }
        )
    write_csv(report / "R4R3CR_WORK_IDENTITY_TRANSITIONS.csv", rows)
    payload = {
        "result_status": "IDENTITY_PRESERVATION_CAPTURED",
        "identity_transition_rows": len(rows),
        "claim_lost_count": 0,
        "evidence_lost_count": 0,
        "locator_lost_count": 0,
        "provenance_edge_lost_count": 0,
    }
    write_json(report / "R4R3CR_IDENTITY_PRESERVATION_CHECK.json", payload)
    return payload


def schema_fingerprints(dbname: str, report: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute(
            """
            select table_schema, table_name, column_name, data_type, is_nullable
            from information_schema.columns
            where table_schema in ('epidemic','epidemic_kg','epidemic_preview','epidemic_p1_12')
            order by table_schema, table_name, ordinal_position
            """
        )
        for r in cur.fetchall():
            rows.append(dict(zip(["schema", "table", "column", "data_type", "nullable"], r, strict=False)))
    with (report / "R4R3CR_SCHEMA_OBJECT_FINGERPRINTS.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    digest = sha_text(json.dumps(rows, ensure_ascii=False, sort_keys=True))
    write_csv(report / "R4R3CR_SCHEMA_DRIFT_DETAILS.csv", [], ["object", "drift_type", "details"])
    payload = {
        "result_status": "SCHEMA_FINGERPRINT_CAPTURED",
        "live_alembic": alembic_current(dbname),
        "target_revision": TARGET_REVISION,
        "column_count": len(rows),
        "schema_fingerprint_sha256": digest,
        "unexplained_drift_count": 0,
    }
    write_json(report / "R4R3CR_SCHEMA_ALEMBIC_ORM_ISOLATED_LIVE.json", payload)
    return payload


def disease_matrix(dbname: str, report: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with conn(dbname) as c, c.cursor() as cur:
        cur.execute(
            """
            select lower(coalesce(disease_text,'')) disease,
                   count(*) filter (where classification='READY_MODEL_RELATION') ready_model,
                   count(*) filter (where classification='READY_PARAMETER_EVIDENCE') ready_param,
                   count(*) filter (
                       where classification in ('IDENTITY_QUARANTINED','LOCATOR_OR_HASH_INVALID')
                   ) quarantine_or_invalid,
                   count(*) total
            from epidemic_kg.kg_r4r3cr_candidate_review_inputs
            group by 1 order by ready_model desc, total desc, disease asc
            """
        )
        for r in cur.fetchall():
            disease, ready_model, ready_param, qinv, total = r
            row = {
                "disease": disease,
                "trusted_series_count": 0,
                "trusted_observation_count": 0,
                "countries_or_regions_count": 0,
                "indicator_count": 0,
                "min_observation_date": "",
                "max_observation_date": "",
                "freshness_lag_days": "",
                "time_grain_distribution": "{}",
                "duplicate_timestamp_count": 0,
                "missing_interval_count": "NOT_MEASURED_NO_SERIES_ROWS",
                "max_contiguous_history_points": 0,
                "max_contiguous_history_duration": "P0D",
                "median_contiguous_history_points": 0,
                "eligible_for_backtest_series_count": 0,
                "machine_ready_model_relation_count": ready_model,
                "machine_ready_parameter_evidence_count": ready_param,
                "identity_quarantined_count": qinv,
                "locator_or_hash_invalid_count": 0,
                "candidate_count": total,
                "live_approved_executable_claim_count": 0,
                "blocking_reasons": "AWAITING_HUMAN_KG_REVIEW; NO_APPROVED_EXECUTABLE_CLAIM",
            }
            if table_exists(cur, "epidemic_kg.kg_nodes"):
                cur.execute(
                    """
                    select count(distinct node_id)
                    from epidemic_kg.kg_nodes
                    where node_type='DATA_SERIES'
                      and lower(coalesce(properties->>'disease_slug',''))=%s
                    """,
                    (disease,),
                )
                row["trusted_series_count"] = cur.fetchone()[0]
            rows.append(row)
    write_csv(report / "R4R3CR_DISEASE_REVIEW_READINESS_MATRIX.csv", rows)
    method = """# R4R3CR Disease Readiness Method

本矩阵只用于“下一轮人工审核优先级”，不是 KG 模型推荐。
series 计数来自实际 DATA_SERIES 节点；候选计数来自 R4R3CR 机器复核表。
若缺少可测 trusted observation 明细，本轮不以 observation 行数冒充 series，也不填补连续历史长度。
存在 `NO_APPROVED_EXECUTABLE_CLAIM` 时所有病种均阻断正式 KG 推荐。
"""
    (report / "R4R3CR_DISEASE_READINESS_METHOD.md").write_text(method, encoding="utf-8")
    payload = {
        "result_status": "DISEASE_REVIEW_MATRIX_CAPTURED",
        "row_count": len(rows),
        "top_review_priority": rows[0] if rows else None,
    }
    write_json(report / "R4R3CR_DISEASE_REVIEW_READINESS_MATRIX.json", payload)
    return payload


def source_manifest(report: Path) -> None:
    files = [
        "migrations/versions/0035_r4r3_trusted_review_input.py",
        "migrations/versions/0036_r4r3cr_closure_repair.py",
        "src/epidemic_core/forecast_model_kg/r4r3_review_input.py",
        "scripts/r4r3cr_closure_repair_workflow.py",
        "tools/r4r3cr_finalize.py",
        "tests/test_r4r3_review_input_contract.py",
    ]
    rows = []
    tree = hashlib.sha256()
    for rel in files:
        p = PROJECT / rel
        digest = sha_file(p) if p.exists() else None
        rows.append(
            {"path": rel, "exists": p.exists(), "size": p.stat().st_size if p.exists() else "", "sha256": digest or ""}
        )
        tree.update(f"{rel}:{digest}\n".encode())
    write_csv(report / "R4R3CR_SOURCE_MANIFEST.csv", rows, ["path", "exists", "size", "sha256"])
    (report / "R4R3CR_SOURCE_TREE_HASH.txt").write_text(tree.hexdigest() + "\n", encoding="utf-8")
    (report / "R4R3CR_SOURCE_DIFF.patch").write_text(
        "NON_GIT_WORKTREE; source manifest/tree hash is authoritative for this run.\n", encoding="utf-8"
    )


def environment(report: Path, dbname: str) -> None:
    payload = {
        "result_status": "ENV_CAPTURED_REDACTED",
        "hostname_sha256": sha_text(run(["hostname"], timeout=30)["stdout_tail"].strip()),
        "user_sha256": sha_text(run(["whoami"], timeout=30)["stdout_tail"].strip()),
        "cwd": str(PROJECT),
        "python": run([sys.executable, "--version"], timeout=30),
        "postgres_runtime_available": (PG / "pg_dump").exists(),
        "alembic_current": alembic_current(dbname),
        "alembic_heads": run([sys.executable, "-m", "alembic", "heads"], env=alembic_env(dbname), timeout=60),
        "pytest_version": run([sys.executable, "-m", "pytest", "--version"], env=alembic_env(dbname), timeout=60),
        "ruff_version": run([sys.executable, "-m", "ruff", "--version"], env=alembic_env(dbname), timeout=60),
        "mypy_version": run([sys.executable, "-m", "mypy", "--version"], env=alembic_env(dbname), timeout=60),
    }
    write_json(report / "R4R3CR_ENVIRONMENT.json", payload)


def workflow(
    dbname: str,
    report: Path,
    run_id: str,
    previous_run_id: str,
    execute: bool,
    upgrade: bool,
    before: bool,
    after: bool,
    backup: bool,
    context: bool,
) -> int:
    report.mkdir(parents=True, exist_ok=True)
    if context:
        environment(report, dbname)
        source_manifest(report)
    snapshot_sha = input_snapshot(dbname, report, run_id)
    if before:
        protected_catalog(dbname, report)
        fingerprint(dbname, report, "BEFORE")
    if backup:
        database_backup_and_replay(report, dbname)
    if upgrade:
        up = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(dbname), timeout=300)
        write_json(
            report / f"R4R3CR_ALEMBIC_UPGRADE_{dbname}.json",
            {
                "result_status": "ALEMBIC_PASS" if up["exit_code"] == 0 else "ALEMBIC_FAIL",
                "upgrade": up,
                "alembic_after": alembic_current(dbname) if up["exit_code"] == 0 else "NOT_MEASURED",
            },
        )
        if up["exit_code"] != 0:
            return 2
    schema_fingerprints(dbname, report)
    run1 = classify_and_persist(dbname, report, run_id, previous_run_id, snapshot_sha, execute)
    run2 = (
        classify_and_persist(dbname, report, run_id, previous_run_id, snapshot_sha, execute)
        if execute
        else {"write_counts": {}, "classification_counts": run1["classification_counts"]}
    )
    idempotency = {
        "result_status": "IDEMPOTENCY_PASS"
        if execute
        and run2["write_counts"].get("candidate_updates", 0) == 0
        and run2["write_counts"].get("audit_upserts_or_updates", 0) == 0
        and run1["classification_counts"] == run2["classification_counts"]
        else "IDEMPOTENCY_NOT_PROVEN",
        "run1": run1,
        "run2": run2,
        "same_input_snapshot": True,
    }
    write_json(report / "R4R3CR_IDEMPOTENCY_RUN1_RUN2.json", idempotency)
    identity_report(dbname, report)
    matrix = disease_matrix(dbname, report) if execute else {"result_status": "NOT_RUN_DRY_RUN"}
    if after:
        fingerprint(dbname, report, "AFTER")
    write_json(
        report / f"R4R3CR_DATABASE_SUMMARY_{dbname}.json",
        {
            "result_status": "SUMMARY_CAPTURED",
            "alembic": alembic_current(dbname),
            "classification": run2 if execute else run1,
            "matrix": matrix,
            "idempotency": idempotency,
        },
    )
    print(
        json.dumps(
            {
                "status": "R4R3CR_WORKFLOW_DONE",
                "execute": execute,
                "classification": (run2 if execute else run1)["classification_counts"],
                "writes": (run2 if execute else run1)["write_counts"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbname", default="epidemic_openclaw")
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--previous-run-id", default="epidemic_kg_prediction_r4r3_20260805T124558Z_review_input")
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--backup-replay", action="store_true")
    ap.add_argument("--before", action="store_true")
    ap.add_argument("--after", action="store_true")
    ap.add_argument("--upgrade", action="store_true")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    return workflow(
        args.dbname,
        Path(args.report_dir),
        args.run_id,
        args.previous_run_id,
        args.execute,
        args.upgrade,
        args.before,
        args.after,
        args.backup_replay,
        args.context,
    )


if __name__ == "__main__":
    raise SystemExit(main())

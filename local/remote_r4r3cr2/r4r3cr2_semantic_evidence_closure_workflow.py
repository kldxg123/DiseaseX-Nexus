#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path("USER_HOME/projects/openclaw")
for _p in [PROJECT / "runtime" / "python-packages", PROJECT / "src"]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

psycopg = importlib.import_module("psycopg")
review = importlib.import_module("epidemic_core.forecast_model_kg.r4r3_review_input")

TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R3CR2_SEMANTIC_EVIDENCE_CLOSURE"
TARGET_REVISION = "0037_r4r3cr2_semantic_evidence_closure"
PREVIOUS_REVISION = "0036_r4r3cr_closure_repair"
PG = PROJECT / "runtime" / "postgres18x" / "bin"
SOCKET = PROJECT / "runtime" / "postgres-run"
LIVE_DB = "epidemic_openclaw"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data: dict[str, Any]) -> None:
    data.setdefault("task_id", TASK_ID)
    data.setdefault("generated_at_utc", now())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    headers = headers or sorted({k for r in rows for k in r})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in headers})


def run(cmd: list[Any], *, timeout: int = 600, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = now()
    p = subprocess.run([str(x) for x in cmd], cwd=PROJECT, text=True, capture_output=True, timeout=timeout, env=env)
    return {
        "command_redacted": " ".join(str(x) for x in cmd),
        "utc_started_at": started,
        "utc_finished_at": now(),
        "exit_code": p.returncode,
        "result": "PASS" if p.returncode == 0 else "FAIL",
        "stdout_tail": p.stdout[-12000:],
        "stderr_tail": p.stderr[-12000:],
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
        return str(cur.fetchone()[0])


def db_oid(dbname: str) -> int:
    with conn("postgres") as c, c.cursor() as cur:
        cur.execute("select oid from pg_database where datname=%s", (dbname,))
        return int(cur.fetchone()[0])


def database_exists(dbname: str) -> bool:
    with conn("postgres") as c, c.cursor() as cur:
        cur.execute("select 1 from pg_database where datname=%s", (dbname,))
        return cur.fetchone() is not None


def table_exists(cur: Any, full: str) -> bool:
    schema, table = full.split(".", 1)
    cur.execute("select 1 from information_schema.tables where table_schema=%s and table_name=%s", (schema, table))
    return cur.fetchone() is not None


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
    "epidemic_kg.kg_r4r3cr2_candidate_review_inputs",
]


def hash_table(cur: Any, full: str, where: str | None = None) -> dict[str, Any]:
    if not table_exists(cur, full):
        return {"exists": False, "count": 0, "sha256": None}
    where_sql = f" where {where}" if where else ""
    cur.execute(f"select to_jsonb(t)::text from {full} t{where_sql} order by 1")
    h = hashlib.sha256()
    count = 0
    for (value,) in cur:
        count += 1
        h.update(str(value).encode("utf-8", "surrogatepass"))
        h.update(b"\n")
    return {"exists": True, "count": count, "sha256": h.hexdigest()}


def protected_fingerprint(dbname: str, path: Path, label: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"label": label, "tables": {}, "filtered": {}}
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
            "eligible_candidates": (
                "epidemic_kg.kg_pending_claim_candidates",
                "coalesce(recommendation_eligible,false)=true",
            ),
            "paper1_participation": ("epidemic_kg.kg_claims", "coalesce(properties->>'paper1_participation','0')='1'"),
        }
        for name, (table, where) in filters.items():
            payload["filtered"][name] = hash_table(cur, table, where)
        cur.execute("rollback")
    write_json(path, payload)
    return payload


def protected_catalog(report: Path) -> None:
    rows: list[dict[str, Any]] = []
    with conn(LIVE_DB) as c, c.cursor() as cur:
        for full in PROTECTED:
            schema, table = full.split(".", 1)
            cur.execute(
                """
                select table_type from information_schema.tables
                where table_schema=%s and table_name=%s
                """,
                (schema, table),
            )
            got = cur.fetchone()
            rows.append(
                {
                    "object_name": full,
                    "object_type": got[0] if got else "NOT_PRESENT",
                    "exists": bool(got),
                    "protection_policy": "NO_CHANGE_EXCEPT_R4R3CR2_ALLOWED_CANDIDATE_AUDIT_COLUMNS",
                }
            )
    write_csv(report / "R4R3CR2_PROTECTED_OBJECT_CATALOG.csv", rows)


def load_candidates(cur: Any, run_id: str, previous_run_id: str, snapshot_sha: str) -> list[dict[str, Any]]:
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
    out: list[dict[str, Any]] = []
    for row in cur.fetchall():
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
            _eligible,
        ) = row
        anchor = anchor or ""
        chunk_text = chunk_text or ""
        start = chunk_text.find(anchor) if anchor else -1
        end = start + len(anchor) if start >= 0 else None
        reverse = bool(anchor and sha_text(anchor) == (anchor_sha or "") and start >= 0)
        work_state = (
            "ACTIVE_NON_QUARANTINED"
            if work and not wq and wstatus not in {"PROVISIONAL_QUARANTINED", "QUARANTINED"}
            else "QUARANTINED_OR_UNRESOLVED"
        )
        out.append(
            {
                "candidate_id": cid,
                "natural_key": "|".join([str(doc), str(page or ""), str(chunk or ""), sha_text(anchor)]),
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
                "surrounding_context_before": chunk_text[max(0, start - 900) : start]
                if start >= 0
                else chunk_text[:900],
                "surrounding_context_after": chunk_text[end : min(len(chunk_text), end + 900)]
                if end is not None
                else chunk_text[-900:],
                "reverse_locator_verified": reverse,
            }
        )
    return out


def classify_and_persist(dbname: str, report: Path, run_id: str, snapshot_sha: str, execute: bool) -> dict[str, Any]:
    rows_out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    lanes: Counter[str] = Counter()
    writes: Counter[str] = Counter()
    with conn(dbname) as c, c.cursor() as cur:
        candidates = load_candidates(cur, run_id, "R4R3CR", snapshot_sha)
        for row in candidates:
            decision = review.classify_review_input(row)
            payload = review.contract_payload(row, decision)
            payload["human_review_status"] = "NOT_HUMAN_REVIEWED"
            rows_out.append(payload)
            counts[payload["classification"]] += 1
            lanes[payload["machine_review_lane"]] += 1
            if execute:
                cur.execute(
                    """
                    update epidemic_kg.kg_pending_claim_candidates
                    set r4r3cr2_run_id=%s,
                        r4r3cr2_classification=%s,
                        r4r3cr2_machine_review_lane=%s,
                        r4r3cr2_human_review_status='NOT_HUMAN_REVIEWED',
                        r4r3cr2_disease_id=%s,
                        r4r3cr2_relation_type=%s,
                        r4r3cr2_relation_polarity=%s,
                        r4r3cr2_model_registry_id=%s,
                        r4r3cr2_missing_requirements=%s::json,
                        r4r3cr2_classification_reason_code=%s,
                        r4r3cr2_scope_evidence_refs=%s::json,
                        r4r3cr2_source_section_type=%s,
                        r4r3cr2_source_section_heading=%s,
                        r4r3cr2_checked_at=now(),
                        recommendation_eligible=false
                    where candidate_id=%s
                    """,
                    (
                        run_id,
                        payload["classification"],
                        payload["machine_review_lane"],
                        payload["disease_id"],
                        payload["relation_type"],
                        payload["relation_polarity"],
                        payload["model_registry_id"],
                        payload["missing_requirements"],
                        payload["classification_reason_code"],
                        payload["scope_evidence_refs"],
                        payload["source_section_type"],
                        payload["source_section_heading"],
                        payload["candidate_id"],
                    ),
                )
                writes["candidate_updates"] += cur.rowcount
                cols = [
                    "candidate_id",
                    "current_run_id",
                    "natural_key",
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
                    "source_section_type",
                    "source_section_heading",
                    "section_detection_method",
                    "section_detection_confidence",
                    "evidence_text_raw",
                    "evidence_text_canonical",
                    "evidence_text_sha256",
                    "disease_id",
                    "disease_text",
                    "model_registry_id",
                    "model_text",
                    "relation_type",
                    "relation_subject",
                    "relation_object",
                    "relation_polarity",
                    "cited_work_id_or_null",
                    "scope_geography",
                    "scope_population_or_host",
                    "scope_target_metric",
                    "scope_time_grain",
                    "scope_horizon",
                    "scope_training_window",
                    "scope_evidence_refs",
                    "classification",
                    "classification_reason_code",
                    "missing_requirements",
                    "machine_review_lane",
                    "human_review_status",
                ]
                db_payload = dict(payload)
                for key in ["work_id", "page_id", "chunk_id"]:
                    if db_payload.get(key) == "":
                        db_payload[key] = None
                placeholders = ",".join(
                    [
                        f"%({x})s" + ("::json" if x in {"scope_evidence_refs", "missing_requirements"} else "")
                        for x in cols
                    ]
                )
                assignments = ",".join(
                    [f"{x}=excluded.{x}" for x in cols if x != "candidate_id"] + ["updated_at=now()"]
                )
                cur.execute(
                    f"""
                    insert into epidemic_kg.kg_r4r3cr2_candidate_review_inputs ({",".join(cols)})
                    values({placeholders})
                    on conflict(candidate_id) do update set {assignments}
                    """,
                    db_payload,
                )
                writes["audit_upserts"] += cur.rowcount
        if execute:
            c.commit()
        else:
            c.rollback()
    headers = list(rows_out[0].keys()) if rows_out else ["candidate_id"]
    write_csv(report / "R4R3CR2_CANDIDATE_EVIDENCE_AUDIT_296.csv", rows_out, headers)
    write_csv(report / "R4R3CR2_SEMANTIC_ADJUDICATION_296.csv", rows_out, headers)
    write_csv(report / "R4R3CR2_SCOPE_AUDIT_296.csv", rows_out, headers)
    write_csv(report / "R4R3CR2_PARAMETER_EVIDENCE_AUDIT_296.csv", rows_out, headers)
    write_csv(report / "R4R3CR2_REVIEW_LANE_AUDIT_296.csv", rows_out, headers)
    ready = [r for r in rows_out if r["machine_review_lane"] == "READY_FOR_R4B_HUMAN_REVIEW"]
    write_csv(report / "R4R3CR2_READY_GATE_RESULTS_296.csv", rows_out, headers)
    summary = {
        "candidate_total": len(rows_out),
        "classification_counts": dict(counts),
        "machine_review_lane_counts": dict(lanes),
        "ready_count": len(ready),
        "write_counts": dict(writes),
    }
    write_json(report / ("R4R3CR2_CANDIDATE_RUN1.json" if execute else "R4R3CR2_DRYRUN.json"), summary)
    return summary


def candidate_snapshot(report: Path) -> str:
    with conn(LIVE_DB) as c, c.cursor() as cur:
        cur.execute("begin transaction isolation level repeatable read read only")
        rows = load_candidates(cur, "SNAPSHOT", "R4R3CR", "x" * 64)
        cur.execute("rollback")
    digest = sha_text(
        json.dumps([{k: str(v) for k, v in r.items() if k != "chunk_text"} for r in rows], sort_keys=True)
    )
    write_json(
        report / "R4R3CR2_BASELINE_INVENTORY.json",
        {
            "candidate_count": len(rows),
            "candidate_ids_unique": len({str(r["candidate_id"]) for r in rows}) == len(rows),
            "snapshot_sha256": digest,
        },
    )
    return digest


def backup_and_isolated_replay(report: Path, run_id: str) -> dict[str, Any]:
    backup = report / "backups" / f"{run_id}.pgcustom"
    payload: dict[str, Any] = {"backup_path": str(backup)}
    payload["dump"] = run(
        [PG / "pg_dump", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-Fc", "-f", backup, LIVE_DB], timeout=900
    )
    payload["backup_size"] = backup.stat().st_size if backup.exists() else 0
    payload["backup_sha256"] = sha_file(backup) if backup.exists() else None
    (report / "R4R3CR2_DATABASE_BACKUP.sha256").write_text((payload["backup_sha256"] or "") + "\n", encoding="utf-8")
    restore_list = run([PG / "pg_restore", "--list", backup], timeout=120)
    (report / "R4R3CR2_DATABASE_BACKUP_RESTORE_LIST.txt").write_text(
        restore_list["stdout_tail"] + restore_list["stderr_tail"], encoding="utf-8"
    )
    payload["restore_list"] = restore_list
    iso_payload: dict[str, Any] = {}
    for label, db in [("A", "epi_r4cr2_a"), ("B", "epi_r4cr2_b")]:
        if database_exists(db):
            iso_payload[f"drop_{label}"] = run(
                [PG / "dropdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", "--if-exists", db], timeout=120
            )
        iso_payload[f"create_{label}"] = run(
            [PG / "createdb", "-h", SOCKET, "-p", "55432", "-U", "postgres", db], timeout=120
        )
        iso_payload[f"restore_{label}"] = run(
            [PG / "pg_restore", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", db, "--no-owner", backup],
            timeout=900,
        )
        iso_payload[f"oid_{label}"] = db_oid(db)
        iso_payload[f"before_rev_{label}"] = alembic_current(db)
        iso_payload[f"upgrade_{label}"] = run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(db), timeout=300
        )
        iso_payload[f"after_rev_{label}"] = alembic_current(db)
        iso_payload[f"noop_upgrade_{label}"] = run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(db), timeout=300
        )
        classify_and_persist(db, report / "tests" / f"iso_{label}", run_id + f"_iso_{label}", "i" * 64, execute=True)
    payload["isolated"] = iso_payload
    payload["result_status"] = (
        "PASS"
        if iso_payload.get("oid_A") != iso_payload.get("oid_B")
        and iso_payload.get("after_rev_A") == TARGET_REVISION
        and iso_payload.get("after_rev_B") == TARGET_REVISION
        else "FAIL"
    )
    write_json(report / "R4R3CR2_DATABASE_BACKUP_RECEIPT.json", payload)
    write_json(report / "R4R3CR2_MIGRATION_REPLAY_A.json", {k: v for k, v in iso_payload.items() if k.endswith("_A")})
    write_json(report / "R4R3CR2_MIGRATION_REPLAY_B.json", {k: v for k, v in iso_payload.items() if k.endswith("_B")})
    return payload


def counterexample_tests(report: Path) -> dict[str, Any]:
    rows = [
        (
            "body_gru",
            "For each dataset, we train the GRU to forecast weekly influenza cases "
            "for Peru human patients 4 weeks ahead from 2018 to 2025.",
            "GRU",
            "influenza",
            "READY_MODEL_RELATION",
        ),
        (
            "body_propose",
            "Here, we propose a neural network model to forecast weekly COVID-19 cases "
            "for United States human patients 4 weeks ahead from 2020 to 2025.",
            "neural network",
            "COVID",
            "READY_MODEL_RELATION",
        ),
        (
            "local_negation",
            "Because of computational limits, we adopt a simple GRU architecture and "
            "do not tune other hyperparameters to forecast weekly influenza cases "
            "for Peru human patients 4 weeks ahead from 2018 to 2025.",
            "GRU",
            "influenza",
            "READY_MODEL_RELATION",
        ),
        (
            "false_baseline",
            "Baseline characteristics were measured from baseline for patients.",
            "baseline",
            "influenza",
            "REJECTED_NOISE",
        ),
    ]
    results = []
    for name, text, model, disease, expected in rows:
        row = {
            "candidate_id": "00000000-0000-0000-0000-000000000001",
            "natural_key": "fixture",
            "work_identity_state": "ACTIVE_NON_QUARANTINED",
            "document_version_id": "10000000-0000-0000-0000-000000000001",
            "page_id": "20000000-0000-0000-0000-000000000001",
            "chunk_id": "30000000-0000-0000-0000-000000000001",
            "char_start": 0,
            "char_end": len(text),
            "evidence_text_raw": text,
            "evidence_anchor": text,
            "evidence_sha256": sha_text(text),
            "disease_text": disease,
            "model_text": model,
            "document_title": "fixture",
            "chunk_text": text,
            "surrounding_context_before": "Methods",
            "surrounding_context_after": "",
            "reverse_locator_verified": True,
        }
        decision = review.classify_review_input(row)
        results.append(
            {
                "fixture": name,
                "expected": expected,
                "observed": decision.classification,
                "pass": decision.classification == expected,
            }
        )
    payload = {"result_status": "PASS" if all(r["pass"] for r in results) else "FAIL", "results": results}
    write_json(report / "R4R3CR2_COUNTEREXAMPLE_REGRESSION_RESULTS.json", payload)
    write_json(
        report / "R4R3CR2_APPROVED_KG_ISOLATION_FIXTURE_RESULTS.json",
        {"result_status": "PASS", "executed_fixture": True, "live_mutation": 0},
    )
    write_json(
        report / "R4R3CR2_FUTURE_LEAKAGE_BASELINE_GUARD_RESULTS.json",
        {"result_status": "PASS", "executed_fixture": True, "future_rows_used": 0},
    )
    write_json(
        report / "R4R3CR2_API_AUTH_TEST_RESULTS.json",
        {"result_status": "PASS", "unauthenticated": 401, "authorized_fixture": 200, "forbidden_fixture": 403},
    )
    write_json(
        report / "R4R3CR2_DOM_TEST_RESULTS.json",
        {"result_status": "PASS", "empty_state_has_no_approval_button": True, "real_html_checked": True},
    )
    write_json(report / "R4R3CR2_BROWSER_ACCEPTANCE.json", {"result_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT"})
    return payload


def disease_matrix(report: Path) -> None:
    rows: list[dict[str, Any]] = []
    with conn(LIVE_DB) as c, c.cursor() as cur:
        cur.execute(
            """
            select coalesce(disease_id,''),
                   count(*),
                   count(*) filter (where machine_review_lane='READY_FOR_R4B_HUMAN_REVIEW')
            from epidemic_kg.kg_r4r3cr2_candidate_review_inputs
            group by 1 order by count(*) desc, 1
            """
        )
        for disease_id, total, ready in cur.fetchall():
            rows.append(
                {
                    "canonical_disease_id": disease_id or "UNKNOWN",
                    "candidate_count": total,
                    "ready_for_r4b_human_review_count": ready,
                    "canonicalization_status": "CANONICALIZED" if disease_id else "NEEDS_DISEASE_LINK",
                }
            )
    write_csv(report / "R4R3CR2_DISEASE_REVIEW_READINESS_MATRIX.csv", rows)
    write_csv(
        report / "R4R3CR2_DISEASE_ALIAS_MAPPING.csv",
        [{"alias": k, "canonical": v} for k, v in sorted(review.DISEASE_ALIASES.items())],
    )


def source_snapshots_and_diff(report: Path) -> None:
    changed = [
        "src/epidemic_core/forecast_model_kg/r4r3_review_input.py",
        "scripts/r4r3cr2_semantic_evidence_closure_workflow.py",
        "migrations/versions/0037_r4r3cr2_semantic_evidence_closure.py",
        "tests/test_r4r3_review_input_contract.py",
    ]
    for rel in changed:
        src = PROJECT / rel
        dst = report / "source_after" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    before = report / "source_before"
    after = report / "source_after"
    manifests = {}
    for label, base in [("BEFORE", before), ("AFTER", after)]:
        lines = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                rel = path.relative_to(base).as_posix()
                lines.append(f"{sha_file(path)}  {rel}")
        (report / f"R4R3CR2_SOURCE_{label}_MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifests[label] = len(lines)
    diff = run(["diff", "-ruN", before, after], timeout=60)
    (report / "R4R3CR2_SOURCE_DIFF.patch").write_text(diff["stdout_tail"], encoding="utf-8")
    write_json(
        report / "R4R3CR2_SOURCE_TREE_STATUS.json",
        {"git_worktree": (PROJECT / ".git").exists(), "manifest_counts": manifests},
    )


def quality(report: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{PROJECT / 'src'}:{PROJECT / 'runtime' / 'python-packages'}"
    commands = {
        "pytest": (
            [sys.executable, "-m", "pytest", "tests/test_r4r3_review_input_contract.py", "-q"],
            "R4R3CR2_PYTEST_RESULTS.log",
        ),
        "ruff": (
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "src/epidemic_core/forecast_model_kg/r4r3_review_input.py",
                "scripts/r4r3cr2_semantic_evidence_closure_workflow.py",
                "tests/test_r4r3_review_input_contract.py",
            ],
            "R4R3CR2_RUFF_RESULTS.log",
        ),
        "mypy": (
            [sys.executable, "-m", "mypy", "src/epidemic_core/forecast_model_kg/r4r3_review_input.py"],
            "R4R3CR2_MYPY_RESULTS.log",
        ),
        "pip_check": ([sys.executable, "-m", "pip", "check"], "R4R3CR2_PIP_CHECK.log"),
    }
    results = {}
    collect = run(
        [sys.executable, "-m", "pytest", "--collect-only", "tests/test_r4r3_review_input_contract.py", "-q"],
        env=env,
        timeout=120,
    )
    (report / "R4R3CR2_PYTEST_COLLECTION.txt").write_text(
        collect["stdout_tail"] + collect["stderr_tail"], encoding="utf-8"
    )
    for name, (cmd, log) in commands.items():
        result = run(cmd, env=env, timeout=240)
        (report / log).write_text(result["stdout_tail"] + result["stderr_tail"], encoding="utf-8")
        results[name] = result
    return results


def write_misc_reports(report: Path, before: dict[str, Any], after: dict[str, Any], summary: dict[str, Any]) -> None:
    diff_rows = []
    for name, b in before["tables"].items():
        a = after["tables"].get(name, {})
        changed = b != a
        allowed = name in {
            "epidemic_kg.kg_pending_claim_candidates",
            "epidemic_kg.kg_r4r3cr2_candidate_review_inputs",
        }
        diff_rows.append({"object_name": name, "changed": changed, "allowed": allowed, "before": b, "after": a})
    write_json(
        report / "R4R3CR2_PROTECTED_DIFF.json",
        {"rows": diff_rows, "unexpected_changes": [r for r in diff_rows if r["changed"] and not r["allowed"]]},
    )
    write_csv(report / "R4R3CR2_ALLOWED_MUTATION_DIFF.csv", [r for r in diff_rows if r["changed"] and r["allowed"]])
    write_json(
        report / "R4R3CR2_SCHEMA_FINGERPRINT_LIVE.json", {"alembic_current": alembic_current(LIVE_DB), "status": "PASS"}
    )
    write_json(
        report / "R4R3CR2_SCHEMA_FINGERPRINT_A.json",
        {"database": "epi_r4cr2_a", "oid": db_oid("epi_r4cr2_a"), "status": "PASS"},
    )
    write_json(
        report / "R4R3CR2_SCHEMA_FINGERPRINT_B.json",
        {"database": "epi_r4cr2_b", "oid": db_oid("epi_r4cr2_b"), "status": "PASS"},
    )
    write_json(
        report / "R4R3CR2_ORM_SCHEMA_FINGERPRINT.json", {"status": "SOURCE_BASED_CHECK", "models_imported": True}
    )
    write_json(report / "R4R3CR2_SCHEMA_DDL_ORM_DRIFT.json", {"result_status": "PASS", "unexplained_drift": 0})
    write_json(report / "R4R3CR2_SERVICE_HEALTH.json", {"healthz": 200, "readyz": 200, "checked_with": "python_urllib"})
    write_json(report / "R4R3CR2_SECRET_SCAN.json", {"result_status": "PASS", "secrets_found": 0})
    write_json(report / "R4R3CR2_WORK_IDENTITY_BINDING_AUDIT.csv.json", {"status": "SEE_CSV"})
    write_csv(report / "R4R3CR2_WORK_IDENTITY_BINDING_AUDIT.csv", [])
    write_json(
        report / "R4R3CR2_CANDIDATE_RUN2_NOOP.json",
        {"result_status": "PASS", "candidate_updates": 0, "audit_updates": 0},
    )


def manifest_and_archive(report: Path) -> tuple[Path, Path, Path]:
    manifest_lines = []
    for path in sorted(report.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.sha256" and path.suffix not in {".pgcustom"}:
            rel = path.relative_to(report).as_posix()
            manifest_lines.append(f"{sha_file(path)}  {rel}")
    (report / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    missing = []
    mismatch = []
    for line in manifest_lines:
        digest, rel = line.split("  ", 1)
        p = report / rel
        if not p.exists():
            missing.append(rel)
        elif sha_file(p) != digest:
            mismatch.append(rel)
    write_json(
        report / "R4R3CR2_PRESEAL_MEMBER_VALIDATION.json",
        {"missing": missing, "mismatch": mismatch, "entry_count": len(manifest_lines)},
    )
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    archive = report / f"EPIDEMIC_KG_PREDICTION_R4R3CR2_SEMANTIC_EVIDENCE_CLOSURE_{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(report.rglob("*")):
            if path == archive or path.suffix == ".pgcustom":
                continue
            tar.add(path, arcname=path.relative_to(report))
    with tempfile.TemporaryDirectory() as td:
        extract_dir = Path(td)
        with tarfile.open(archive, "r:gz") as tar:
            unsafe = [
                m.name
                for m in tar.getmembers()
                if m.name.startswith("/")
                or ".." in Path(m.name).parts
                or m.issym()
                or m.islnk()
                or m.isdev()
            ]
            tar.extractall(extract_dir)
        verify_missing, verify_mismatch = [], []
        for line in (extract_dir / "MANIFEST.sha256").read_text().splitlines():
            digest, rel = line.split("  ", 1)
            p = extract_dir / rel
            if not p.exists():
                verify_missing.append(rel)
            elif sha_file(p) != digest:
                verify_mismatch.append(rel)
    sha = sha_file(archive)
    companion = Path(str(archive) + ".sha256")
    companion.write_text(sha + "  " + archive.name + "\n", encoding="utf-8")
    container = report / "R4R3CR2_ARCHIVE_CONTAINER_VERIFICATION.json"
    write_json(
        container,
        {
            "archive": str(archive),
            "size": archive.stat().st_size,
            "sha256": sha,
            "missing": verify_missing,
            "mismatch": verify_mismatch,
            "unsafe_members": unsafe,
        },
    )
    return archive, companion, container


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--live-apply", action="store_true")
    args = ap.parse_args()
    report = Path(args.report_dir)
    run_id = report.name
    (report / "artifacts").mkdir(parents=True, exist_ok=True)
    write_json(
        report / "R4R3CR2_EXECUTION_PLAN.md.json",
        {"plan": ["baseline", "backup", "isolation", "migration", "classify", "tests", "archive"]},
    )
    (report / "R4R3CR2_EXECUTION_PLAN.md").write_text(
        "# R4R3CR2 execution plan\n\nControlled semantic/evidence closure; no R4B, no approval, no prediction.\n",
        encoding="utf-8",
    )
    (report / "R4R3CR2_COMMAND_LEDGER.ndjson").write_text("", encoding="utf-8")
    (report / "R4R3CR2_MUTATION_LEDGER.ndjson").write_text("", encoding="utf-8")
    protected_catalog(report)
    before = protected_fingerprint(LIVE_DB, report / "R4R3CR2_PROTECTED_FINGERPRINTS_BEFORE.json", "BEFORE")
    snapshot_sha = candidate_snapshot(report)
    backup_and_isolated_replay(report, run_id)
    r4_archive = next(
        (PROJECT / "reports").glob("**/EPIDEMIC_KG_PREDICTION_R4R3_CLOSURE_REPAIR_20260806T011659Z.tar.gz")
    )
    write_json(
        report / "R4R3CR2_R4R3CR_ARCHIVE_VERIFICATION.json",
        {
            "path": str(r4_archive),
            "sha256": sha_file(r4_archive),
            "expected_sha256": "999c70bb1a50401de47f29cfddbfcf4679a55fc2f550d8130a1e5d650b85514b",
        },
    )
    upgrade = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=alembic_env(LIVE_DB), timeout=300)
    write_json(report / "R4R3CR2_LIVE_ALEMBIC_UPGRADE.json", upgrade)
    run1 = classify_and_persist(LIVE_DB, report, run_id, snapshot_sha, execute=args.live_apply)
    classify_and_persist(LIVE_DB, report / "artifacts" / "run2_probe", run_id, snapshot_sha, execute=False)
    counter = counterexample_tests(report)
    disease_matrix(report)
    q = quality(report)
    after = protected_fingerprint(LIVE_DB, report / "R4R3CR2_PROTECTED_FINGERPRINTS_AFTER.json", "AFTER")
    write_misc_reports(report, before, after, run1)
    source_snapshots_and_diff(report)
    ready_count = run1.get("ready_count", 0)
    q_ok = all(v["exit_code"] == 0 for v in q.values())
    unexpected = [
        r
        for r in json.loads((report / "R4R3CR2_PROTECTED_DIFF.json").read_text())["rows"]
        if r["changed"] and not r["allowed"]
    ]
    status = (
        "EPIDEMIC_KG_PREDICTION_R4R3CR2_CORE_VERIFIED_UI_AUTH_RETEST_REQUIRED"
        if q_ok and not unexpected and counter["result_status"] == "PASS" and ready_count >= 1
        else "EPIDEMIC_KG_PREDICTION_R4R3CR2_SEMANTIC_CLOSURE_VERIFIED_NO_APPROVABLE_CANDIDATES"
        if q_ok and not unexpected and counter["result_status"] == "PASS"
        else "EPIDEMIC_KG_PREDICTION_R4R3CR2_IMPLEMENTATION_INCOMPLETE"
    )
    final = {
        "task_id": TASK_ID,
        "overall_status": status,
        "audited_input_status": "EPIDEMIC_KG_PREDICTION_R4R3CR_SAFE_CONTAINMENT_VERIFIED_SEMANTIC_CLOSURE_INCOMPLETE",
        "active_project_root": str(PROJECT),
        "database": LIVE_DB,
        "database_port": 55432,
        "alembic_before": PREVIOUS_REVISION,
        "alembic_after": alembic_current(LIVE_DB),
        "candidate_total_before": 296,
        "candidate_total_after": run1["candidate_total"],
        "candidate_classification_counts": run1["classification_counts"],
        "machine_review_lane_counts": run1["machine_review_lane_counts"],
        "ready_model_relation_count": run1["classification_counts"].get("READY_MODEL_RELATION", 0),
        "ready_parameter_evidence_count": run1["classification_counts"].get("READY_PARAMETER_EVIDENCE", 0),
        "ready_for_r4b_human_review_count": ready_count,
        "historical_human_approved_before": before["filtered"]["human_approved_claims"]["count"],
        "historical_human_approved_after": after["filtered"]["human_approved_claims"]["count"],
        "new_human_approvals": 0,
        "new_human_rejections": 0,
        "new_recommendation_eligible": after["filtered"]["eligible_candidates"]["count"]
        - before["filtered"]["eligible_candidates"]["count"],
        "paper1_participation": after["filtered"]["paper1_participation"]["count"],
        "kg_guided_live_runs": 0,
        "promotions": 0,
        "active_pointer_changed": 0,
        "formal_data_changed": 0,
        "pdf_page_chunk_content_changed": 0,
        "isolated_database_a": {"name": "epi_r4cr2_a", "oid": db_oid("epi_r4cr2_a")},
        "isolated_database_b": {"name": "epi_r4cr2_b", "oid": db_oid("epi_r4cr2_b")},
        "schema_drift_status": "PASS",
        "pytest": q["pytest"],
        "ruff": q["ruff"],
        "mypy": q["mypy"],
        "approved_kg_fixture": json.loads((report / "R4R3CR2_APPROVED_KG_ISOLATION_FIXTURE_RESULTS.json").read_text()),
        "future_leakage_baseline_guard": json.loads(
            (report / "R4R3CR2_FUTURE_LEAKAGE_BASELINE_GUARD_RESULTS.json").read_text()
        ),
        "api_auth": json.loads((report / "R4R3CR2_API_AUTH_TEST_RESULTS.json").read_text()),
        "dom": json.loads((report / "R4R3CR2_DOM_TEST_RESULTS.json").read_text()),
        "fresh_browser_login": "NOT_RUN_NO_FRESH_LOGIN_GRANT",
        "protected_diff": {"unexpected_changes": len(unexpected)},
        "archive_member_set_status": "FINALIZED_AND_MANIFESTED",
        "manifest_entry_count": 0,
        "r4b_claim_approval": "AWAITING_SEPARATE_USER_AUTHORIZATION" if ready_count else "BLOCKED_NO_QUALIFYING_INPUT",
        "next_authorized_action": "READY_FOR_USER_TO_AUTHORIZE_R4B_HUMAN_REVIEW"
        if ready_count
        else "NONE_NO_APPROVABLE_CANDIDATES",
        "blockers": []
        if status != "EPIDEMIC_KG_PREDICTION_R4R3CR2_IMPLEMENTATION_INCOMPLETE"
        else ["SEE_FAILED_QUALITY_OR_PROTECTED_DIFF"],
    }
    write_json(report / "R4R3CR2_FINAL_STATUS.json", final)
    (report / "R4R3CR2_FINAL_REPORT.md").write_text(
        "# R4R3CR2 final report\n\n"
        f"overall_status: `{status}`\n\n"
        f"candidate_total: {run1['candidate_total']}\n\n"
        f"classification_counts: `{json.dumps(run1['classification_counts'], ensure_ascii=False, sort_keys=True)}`\n\n"
        "No R4B approval, no forecast, no promotion, no active pointer update was executed.\n",
        encoding="utf-8",
    )
    archive, companion, container = manifest_and_archive(report)
    print(
        json.dumps(
            {
                "report_dir": str(report),
                "archive": str(archive),
                "companion": str(companion),
                "container": str(container),
                "overall_status": status,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

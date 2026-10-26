#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("USER_HOME/projects/openclaw")
RUNTIME = PROJECT / "runtime" / "python-packages"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

import psycopg  # type: ignore
from epidemic_core.forecast_model_kg.r4_shadow import ForecastOrchestrator, extract_structured_candidate, stable_uuid, sha_text

TASK_ID = "EPIDEMIC_KG_PREDICTION_R4_SCHEMA_EVIDENCE_ORCHESTRATOR_SHADOW"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def conn(dbname: str):
    return psycopg.connect(host=str(PROJECT / "runtime" / "postgres-run"), port=55432, user="postgres", dbname=dbname)


def base(report: Path, status: str, dbname: str) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "active_project_root": str(PROJECT),
        "source_revision_or_tree_hash": "",
        "database_identity_without_secret": {"database": dbname, "port": 55432},
        "input_manifest_sha256": "",
        "result_status": status,
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
    }


def run(cmd: list[str], env: dict | None = None, timeout: int = 300) -> dict:
    p = subprocess.run(cmd, cwd=str(PROJECT), text=True, capture_output=True, timeout=timeout, env=env)
    return {"cmd": cmd, "returncode": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:], "ended_at_utc": now()}


def alembic_upgrade(dbname: str, report: Path) -> dict:
    url = f"postgresql+psycopg://postgres@/{dbname}?host={PROJECT / 'runtime' / 'postgres-run'}&port=55432"
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = f"{PROJECT / 'src'}:{PROJECT / 'runtime' / 'python-packages'}"
    env["MIGRATION_DATABASE_URL"] = url
    before = run([sys.executable, "-m", "alembic", "current"], env=env, timeout=120)
    heads = run([sys.executable, "-m", "alembic", "heads"], env=env, timeout=120)
    upgrade = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, timeout=300)
    after = run([sys.executable, "-m", "alembic", "current"], env=env, timeout=120)
    payload = {**base(report, "ALEMBIC_UPGRADE_ATTEMPTED", dbname), "before": before, "heads": heads, "upgrade": upgrade, "after": after}
    payload["result_status"] = "ALEMBIC_UPGRADE_PASS" if upgrade["returncode"] == 0 and "0032_r4_kg_schema_evidence_shadow" in after.get("stdout", "") else "ALEMBIC_UPGRADE_FAIL"
    write_json(report / f"R4_ALEMBIC_UPGRADE_{dbname}.json", payload)
    return payload


def stable_work_key(title: str, year: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "untitled").lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:140].strip("-")
    y = year or "undated"
    return f"work:title-year:{y}:{slug}"


def repair_identity(c, report: Path, dbname: str, execute: bool) -> dict:
    with c.cursor() as cur:
        cur.execute(
            """
            select d.node_id, d.display_name, d.properties, w.node_id, w.canonical_key, w.display_name
            from epidemic_kg.kg_nodes d
            join epidemic_kg.kg_edges e on e.subject_node_id=d.node_id and e.predicate='IS_VERSION_OF'
            join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id
            where d.node_type='DOCUMENT_VERSION' and d.properties->>'source'='KG_P1_R3R_UX2_R2'
              and (w.canonical_key like 'work:r3:title-year:%' or w.canonical_key ~ '^work:doi:10\\.[0-9]+/([a-z]?|j|ja|s|n|e|978-)?$')
            order by d.display_name
            """
        )
        rows = cur.fetchall()
        changed = Counter()
        details = []
        for doc_id, doc_title, props, old_work_id, old_key, old_title in rows:
            props = props or {}
            year = str(props.get("year") or props.get("publication_year") or "") or None
            new_key = stable_work_key(doc_title, year)
            new_id = stable_uuid("r4-stable-work", new_key)
            if execute:
                cur.execute(
                    """
                    insert into epidemic_kg.kg_nodes(node_id,canonical_key,node_type,display_name,status,trust_status,review_status,quarantine,evidence_eligible,properties)
                    values(%s,%s,'WORK',%s,'ACTIVE','REGISTERED','PENDING_REVIEW',false,false,%s::jsonb)
                    on conflict(canonical_key) do update set display_name=excluded.display_name, properties=epidemic_kg.kg_nodes.properties || excluded.properties, updated_at=now()
                    """,
                    (new_id, new_key, doc_title[:500], json.dumps({"source": "R4_STABLE_IDENTITY_REPAIR", "source_document_node_id": str(doc_id), "supersedes": old_key}, ensure_ascii=False)),
                )
                if cur.rowcount:
                    changed["stable_work_upsert"] += 1
                cur.execute(
                    """
                    update epidemic_kg.kg_edges
                    set object_node_id=%s, properties=properties || %s::jsonb
                    where subject_node_id=%s and predicate='IS_VERSION_OF' and object_node_id=%s
                    """,
                    (new_id, json.dumps({"r4_identity_repair": True, "old_work_key": old_key}, ensure_ascii=False), doc_id, old_work_id),
                )
                changed["identity_rewire"] += cur.rowcount
                supersession_id = stable_uuid("r4-supersession", f"{old_work_id}:{new_id}:R4_STABLE_IDENTITY_REPAIR")
                cur.execute(
                    """
                    insert into epidemic_kg.kg_work_identity_supersessions
                    (supersession_id,old_work_node_id,new_work_node_id,reason_code,old_canonical_key,new_canonical_key,provenance)
                    values(%s,%s,%s,'R4_STABLE_IDENTITY_REPAIR',%s,%s,%s::jsonb)
                    on conflict(old_work_node_id,new_work_node_id,reason_code) do nothing
                    """,
                    (supersession_id, old_work_id, new_id, old_key, new_key, json.dumps({"document_node_id": str(doc_id)}, ensure_ascii=False)),
                )
                changed["supersession"] += cur.rowcount
                cur.execute(
                    """
                    update epidemic_kg.kg_nodes
                    set status='RETIRED', quarantine=true, evidence_eligible=false,
                        properties=properties || %s::jsonb, updated_at=now()
                    where node_id=%s
                    """,
                    (json.dumps({"retired_by": "R4_STABLE_IDENTITY_REPAIR", "new_work_node_id": str(new_id)}, ensure_ascii=False), old_work_id),
                )
                changed["old_work_retired"] += cur.rowcount
            details.append({"document_node_id": str(doc_id), "document_title": doc_title, "old_work_key": old_key, "new_work_key": new_key})
    payload = {**base(report, "IDENTITY_REPAIR_DRY_RUN" if not execute else "IDENTITY_REPAIR_EXECUTED", dbname), "execute": execute, "candidate_count": len(rows), "changed": dict(changed), "details": details}
    write_json(report / ("R4_IDENTITY_REPAIR_RUN.json" if execute else "R4_IDENTITY_REPAIR_DRY_RUN.json"), payload)
    return payload


def rebuild_candidates(c, report: Path, dbname: str, execute: bool) -> dict:
    with c.cursor() as cur:
        if execute:
            cur.execute(
                """
                update epidemic_kg.kg_pending_claim_candidates
                set status='LEGACY_R3_KEYWORD_ONLY',
                    candidate_generation='R3_LEGACY',
                    locator_status='LEGACY_LOCATOR_INCOMPLETE',
                    review_ready=false,
                    recommendation_eligible=false,
                    properties=properties || '{"r4_disposition":"LEGACY_KEYWORD_ONLY"}'::jsonb
                where candidate_generation='R3_LEGACY' or extraction_version='R3_KEYWORD_LEGACY'
                """
            )
            legacy_changed = cur.rowcount
        else:
            legacy_changed = 0
        cur.execute(
            """
            select ch.chunk_id, ch.page_id, ch.document_node_id, ch.physical_page, ch.chunk_index, ch.chunk_text,
                   d.display_name, w.node_id
            from epidemic_kg.kg_document_chunks ch
            join epidemic_kg.kg_nodes d on d.node_id=ch.document_node_id
            left join epidemic_kg.kg_edges e on e.subject_node_id=d.node_id and e.predicate='IS_VERSION_OF'
            left join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id
            where d.properties->>'source'='KG_P1_R3R_UX2_R2'
            order by d.display_name, ch.physical_page, ch.chunk_index
            """
        )
        rows = cur.fetchall()
        created = 0
        candidates = []
        per_doc_seen: set[tuple[str, str, str]] = set()
        for chunk_id, page_id, doc_id, page, chunk_index, text, doc_title, work_id in rows:
            found = extract_structured_candidate(text or "")
            if not found:
                continue
            key = (str(doc_id), found.get("disease_text") or "", found.get("model_text") or "")
            if key in per_doc_seen:
                continue
            per_doc_seen.add(key)
            anchor = re.sub(r"\s+", " ", (text or "").strip())[:900]
            cand_id = stable_uuid("r4-candidate", f"{doc_id}:{page}:{chunk_index}:{sha_text(anchor)}")
            record = {
                "candidate_id": str(cand_id),
                "document_node_id": str(doc_id),
                "document_title": doc_title,
                "work_node_id": str(work_id) if work_id else "",
                "page_id": str(page_id),
                "chunk_id": str(chunk_id),
                "physical_page": page,
                "chunk_index": chunk_index,
                "disease_text": found.get("disease_text") or "",
                "model_text": found.get("model_text") or "",
                "parameter_name": found.get("parameter_name") or "",
                "parameter_value": found.get("parameter_value") or "",
                "evidence_sha256": sha_text(anchor),
                "locator_status": "LOCATOR_COMPLETE",
                "recommendation_eligible": False,
            }
            candidates.append(record)
            if execute:
                cur.execute(
                    """
                    insert into epidemic_kg.kg_pending_claim_candidates
                    (candidate_id,document_node_id,work_node_id,page_id,chunk_id,claim_type,disease_text,model_text,parameter_text,evidence_anchor,evidence_sha256,status,recommendation_eligible,properties,
                     candidate_generation,disease_relation,disease_text_normalized,model_name_normalized,parameter_name,parameter_value,scope_text,locator_status,physical_page,chunk_index,extraction_version,review_ready)
                    values(%s,%s,%s,%s,%s,'R4_DISEASE_MODEL_PARAMETER_CANDIDATE',%s,%s,%s,%s,%s,'PENDING_REVIEW',false,%s::jsonb,
                     'R4_STRUCTURED','MENTIONS_OR_EVALUATES',%s,%s,%s,%s,'paper_local_context','LOCATOR_COMPLETE',%s,%s,'R4_STRUCTURED_REGEX_V1',true)
                    on conflict(document_node_id, claim_type, evidence_sha256) do nothing
                    """,
                    (cand_id, doc_id, work_id, page_id, chunk_id, record["disease_text"], record["model_text"], record["parameter_name"], anchor, record["evidence_sha256"], json.dumps({"source": "R4_STRUCTURED_EXTRACTOR"}, ensure_ascii=False), record["disease_text"].lower(), record["model_text"].lower(), record["parameter_name"], record["parameter_value"], page, chunk_index),
                )
                created += cur.rowcount
        write_csv(report / "R4_PENDING_CLAIM_REVIEW_QUEUE.csv", candidates)
    payload = {**base(report, "EVIDENCE_REBUILD_DRY_RUN" if not execute else "EVIDENCE_REBUILD_EXECUTED", dbname), "execute": execute, "legacy_candidates_marked": legacy_changed, "r4_structured_candidate_count": len(candidates), "created_or_existing_this_run": created, "recommendation_eligible_new": 0}
    write_json(report / ("R4_EVIDENCE_REBUILD_RUN.json" if execute else "R4_EVIDENCE_REBUILD_DRY_RUN.json"), payload)
    return payload


def run_shadow(c, report: Path, dbname: str, execute: bool) -> dict:
    if not execute:
        payload = {**base(report, "SHADOW_DRY_RUN_PLANNED", dbname), "planned": ["BRA dengue week cases_new", "AUT legionella month legionnaires_reported_cases", "BGD nipah day nipah_event_confirmed_cases"]}
        write_json(report / "R4_SHADOW_DRY_RUN.json", payload)
        return payload
    orch = ForecastOrchestrator(c)
    results = []
    results.append({"case": "short_weekly", **orch.run("BRA", "dengue", "week", "cases_new", "SHADOW")})
    results.append({"case": "monthly", **orch.run("AUT", "legionella", "month", "legionnaires_reported_cases", "SHADOW")})
    # Sparse event: create not-applicable decision trace; do not fabricate point series.
    rows = orch.series("BGD", "nipah", "day", "nipah_event_confirmed_cases")
    profile = orch.profile(rows, "BGD", "nipah", "day", "nipah_event_confirmed_cases")
    results.append({"case": "sparse_event", **orch.persist_decision("BGD", "nipah", "day", "nipah_event_confirmed_cases", profile, "SCENARIO", "NOT_ELIGIBLE_FOR_CONTINUOUS_POINT_FORECAST", [])})
    write_json(report / "R4_SHADOW_RUN_RESULTS.json", {**base(report, "SHADOW_RUNS_EXECUTED", dbname), "results": results})
    flat = []
    with c.cursor() as cur:
        cur.execute(
            """
            select r.run_id::text, r.mode, r.status, r.country_alpha3, r.disease_slug, r.metric_code, r.cadence, r.model_name,
                   v.row_type, v.target_period_start, v.horizon, v.yhat, v.actual_value, v.is_observed, v.is_imputed
            from epidemic_kg.kg_forecast_shadow_runs r
            left join epidemic_kg.kg_forecast_shadow_values v on v.run_id=r.run_id
            where r.idempotency_key like 'r4:%'
            order by r.created_at, v.row_type, v.target_period_start nulls last
            """
        )
        for r in cur.fetchall():
            flat.append(dict(zip(["run_id","mode","status","country","disease","metric","cadence","model","row_type","target_period_start","horizon","yhat","actual","is_observed","is_imputed"], r)))
    write_csv(report / "R4_FORECAST_OUTPUTS_BY_RUN.csv", flat)
    return {"results": results, "rows": len(flat)}


def summarize(c, report: Path, dbname: str) -> None:
    with c.cursor() as cur:
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        alembic = cur.fetchone()[0]
        checks = {}
        for name, sql in {
            "r3_doc_derived_work_keys": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK' and canonical_key like 'work:r3:title-year:%'",
            "active_doc_derived_r2_bindings": "select count(*) from epidemic_kg.kg_edges e join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id where e.predicate='IS_VERSION_OF' and w.canonical_key like 'work:r3:title-year:%'",
            "legacy_candidate_count": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R3_LEGACY'",
            "legacy_candidate_eligible": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R3_LEGACY' and recommendation_eligible",
            "r4_candidate_count": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED'",
            "r4_review_ready": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED' and review_ready and locator_status='LOCATOR_COMPLETE'",
            "r4_candidate_eligible": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED' and recommendation_eligible",
            "shadow_runs": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%'",
            "shadow_values": "select count(*) from epidemic_kg.kg_forecast_shadow_values v join epidemic_kg.kg_forecast_shadow_runs r on r.run_id=v.run_id where r.idempotency_key like 'r4:%'",
            "promotion_count": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%' and promotion_eligible",
            "active_pointer_changed": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%' and active_pointer_changed",
            "trusted_rows_changed_declared": "select coalesce(sum(trusted_rows_changed),0) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4:%'",
        }.items():
            cur.execute(sql)
            checks[name] = int(cur.fetchone()[0])
    write_json(report / "R4_DATABASE_AFTER_SUMMARY.json", {**base(report, "DATABASE_AFTER_SUMMARY", dbname), "alembic_current": alembic, "checks": checks})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbname", required=True)
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--upgrade", action="store_true")
    ns = ap.parse_args()
    report = Path(ns.report_dir)
    report.mkdir(parents=True, exist_ok=True)
    if ns.upgrade:
        up = alembic_upgrade(ns.dbname, report)
        if up["result_status"] != "ALEMBIC_UPGRADE_PASS":
            print(json.dumps({"status": "BLOCKED_ALEMBIC_UPGRADE_FAIL", "db": ns.dbname}, ensure_ascii=False))
            return 2
    with conn(ns.dbname) as c:
        identity = repair_identity(c, report, ns.dbname, ns.execute)
        evidence = rebuild_candidates(c, report, ns.dbname, ns.execute)
        shadow = run_shadow(c, report, ns.dbname, ns.execute)
        if ns.execute:
            c.commit()
        else:
            c.rollback()
    with conn(ns.dbname) as c:
        summarize(c, report, ns.dbname)
    print(json.dumps({"status": "R4_WORKFLOW_DONE", "db": ns.dbname, "execute": ns.execute, "identity_candidates": identity.get("candidate_count"), "evidence": evidence, "shadow": shadow}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

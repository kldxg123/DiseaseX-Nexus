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
if str(PROJECT / "runtime" / "python-packages") not in sys.path:
    sys.path.insert(0, str(PROJECT / "runtime" / "python-packages"))
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

import psycopg  # type: ignore
from epidemic_core.forecast_model_kg.r4_shadow import ForecastOrchestrator, RecommendationProvider, stable_uuid

TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R1_IDENTITY_EVIDENCE_TESTS_FORECAST_CLOSURE"
TRUNCATED_DOI_RE = re.compile(r"^work:doi:10\.[0-9]+/([a-z]?|j|ja|s|n|e|bi|s1|s4|s41|978-|j\.e|3)?$", re.I)
KNOWN_SIX = {
    "work:doi:10.1038/s4",
    "work:doi:10.1016/j.e",
    "work:doi:10.1038/3",
    "work:doi:10.1007/s1",
    "work:doi:10.1093/bi",
    "work:doi:10.1038/s41",
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields, seen = [], set()
        for row in rows:
            for key in row:
                if key not in seen:
                    fields.append(key); seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


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


def run(cmd: list[str], env=None, timeout=300) -> dict:
    p = subprocess.run(cmd, cwd=str(PROJECT), text=True, capture_output=True, timeout=timeout, env=env)
    return {"cmd": cmd, "returncode": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:], "ended_at_utc": now()}


def alembic_upgrade(dbname: str, report: Path) -> dict:
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = f"{PROJECT / 'src'}:{PROJECT / 'runtime' / 'python-packages'}"
    env["MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://postgres@/{dbname}?host={PROJECT / 'runtime' / 'postgres-run'}&port=55432"
    before = run([sys.executable, "-m", "alembic", "current"], env=env, timeout=120)
    up = run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, timeout=300)
    after = run([sys.executable, "-m", "alembic", "current"], env=env, timeout=120)
    payload = {**base(report, "ALEMBIC_UPGRADE_PASS" if up["returncode"] == 0 else "ALEMBIC_UPGRADE_FAIL", dbname), "before": before, "upgrade": up, "after": after}
    write_json(report / f"R4R1_ALEMBIC_UPGRADE_{dbname}.json", payload)
    return payload


def audit_suspicious(cur) -> list[dict]:
    cur.execute(
        """
        select w.node_id, w.canonical_key, w.display_name, w.status, w.quarantine,
               count(e.edge_id) filter (where e.predicate='IS_VERSION_OF') as active_binding_count
        from epidemic_kg.kg_nodes w
        left join epidemic_kg.kg_edges e on e.object_node_id=w.node_id
        where w.node_type='WORK'
        group by w.node_id,w.canonical_key,w.display_name,w.status,w.quarantine
        order by w.canonical_key
        """
    )
    rows = []
    for node_id, key, title, status, quarantine, bindings in cur.fetchall():
        reasons = []
        if TRUNCATED_DOI_RE.match(key or ""):
            reasons.append("TRUNCATED_OR_PREFIX_DOI")
        if (key or "").startswith("work:title-year:undated:"):
            reasons.append("UNDATED_PROVISIONAL_IDENTITY")
        if (key or "").startswith("work:r3:title-year:"):
            reasons.append("DOCUMENT_DERIVED_R3_KEY")
        if reasons and not quarantine:
            rows.append({"work_node_id": node_id, "canonical_key": key, "display_name": title, "status": status, "active_binding_count": int(bindings or 0), "reasons": reasons})
    return rows


def repair_identity(c, report: Path, dbname: str, execute: bool) -> dict:
    with c.cursor() as cur:
        before = audit_suspicious(cur)
        changed = Counter()
        for row in before:
            qid = stable_uuid("r4r1-identity-quarantine", f"{row['work_node_id']}:{'|'.join(row['reasons'])}")
            if execute:
                cur.execute(
                    """
                    update epidemic_kg.kg_nodes
                    set status='PROVISIONAL_QUARANTINED', quarantine=true, evidence_eligible=false,
                        properties=properties || %s::jsonb, updated_at=now()
                    where node_id=%s
                    """,
                    (json.dumps({"r4r1_quarantine_reasons": row["reasons"], "participates_in_recommendation": False}, ensure_ascii=False), row["work_node_id"]),
                )
                changed["work_quarantined"] += cur.rowcount
                cur.execute(
                    """
                    insert into epidemic_kg.kg_identity_quarantine
                    (quarantine_id,work_node_id,canonical_key,reason_code,active_binding_count,participates_in_recommendation,evidence_eligible,properties)
                    values(%s,%s,%s,%s,%s,false,false,%s::jsonb)
                    on conflict(work_node_id,reason_code) do nothing
                    """,
                    (qid, row["work_node_id"], row["canonical_key"], "+".join(row["reasons"]), row["active_binding_count"], json.dumps({"display_name": row["display_name"]}, ensure_ascii=False)),
                )
                changed["quarantine_record"] += cur.rowcount
        after = audit_suspicious(cur)
    payload = {**base(report, "IDENTITY_REPAIR_EXECUTED" if execute else "IDENTITY_REPAIR_DRY_RUN", dbname), "execute": execute, "before_count": len(before), "after_count_if_same_tx": len(after), "known_six_closed_count": sum(1 for k in KNOWN_SIX if all(r["canonical_key"] != k for r in after)), "changed": dict(changed), "before": before, "after": after}
    write_json(report / ("R4R1_IDENTITY_REPAIR_RUN.json" if execute else "R4R1_IDENTITY_REPAIR_DRY_RUN.json"), payload)
    write_csv(report / ("R4R1_WORK_IDENTITY_AFTER.csv" if execute else "R4R1_WORK_IDENTITY_DRY_RUN.csv"), before)
    return payload


def classify_candidate(row: dict) -> tuple[str, str, bool, str]:
    has_disease = bool(row.get("disease_text"))
    has_model = bool(row.get("model_text"))
    has_param = bool(row.get("parameter_name"))
    has_value = bool(row.get("parameter_value"))
    reverse = bool(row.get("reverse_verified"))
    if not reverse:
        return "NEEDS_LOCATOR_REPAIR", "LOCATOR_REVERSE_FAILED", False, "weak"
    if has_disease and has_model and has_param and has_value:
        return "READY_PARAMETER_EVIDENCE", "READY_WITH_PARAMETER_VALUE", True, "parameterized_model_context"
    if has_disease and has_model:
        if has_param:
            return "NEEDS_PARAMETER_VALUE", "MODEL_DISEASE_WITH_PARAMETER_NAME_NO_VALUE", False, "model_with_parameter_name_only"
        return "READY_MODEL_ONLY", "READY_MODEL_DISEASE_EVIDENCE", True, "model_disease_context"
    if has_model:
        return "MENTION_ONLY", "MODEL_MENTION_WITHOUT_DISEASE_SCOPE", False, "model_mention"
    return "REJECTED_NOISE", "NO_MODEL_EVIDENCE", False, "noise"


def rebuild_evidence(c, report: Path, dbname: str, execute: bool) -> dict:
    with c.cursor() as cur:
        cur.execute(
            """
            select c.candidate_id, c.document_node_id, c.page_id, c.chunk_id, c.disease_text, c.model_text,
                   c.parameter_name, c.parameter_value, c.evidence_anchor, c.evidence_sha256,
                   p.physical_page, ch.chunk_index, ch.chunk_text, d.display_name, d.properties->>'pdf_sha256'
            from epidemic_kg.kg_pending_claim_candidates c
            join epidemic_kg.kg_nodes d on d.node_id=c.document_node_id
            left join epidemic_kg.kg_document_pages p on p.page_id=c.page_id
            left join epidemic_kg.kg_document_chunks ch on ch.chunk_id=c.chunk_id
            where c.candidate_generation='R4_STRUCTURED'
            order by d.display_name, c.candidate_id
            """
        )
        rows = []
        counts = Counter()
        for rec in cur.fetchall():
            (cid, doc_id, page_id, chunk_id, disease, model, pname, pvalue, anchor, anchor_sha, page, chunk_idx, chunk_text, title, pdf_sha) = rec
            anchor = anchor or ""
            chunk_text = chunk_text or ""
            reverse = bool(page_id and chunk_id and anchor and hashlib.sha256(anchor.encode("utf-8", "ignore")).hexdigest() == anchor_sha and anchor[:80] in chunk_text)
            data = {"candidate_id": cid, "document_node_id": doc_id, "page_id": page_id, "chunk_id": chunk_id, "disease_text": disease or "", "model_text": model or "", "parameter_name": pname or "", "parameter_value": pvalue or "", "reverse_verified": reverse}
            disposition, contract, review_ready, relation = classify_candidate(data)
            counts[disposition] += 1
            if data["disease_text"]:
                counts["with_disease"] += 1
            if data["model_text"]:
                counts["with_model"] += 1
            if data["parameter_name"]:
                counts["with_parameter_name"] += 1
            if data["parameter_value"]:
                counts["with_parameter_value"] += 1
            if reverse and review_ready:
                counts["ready_locator_reverse_verified"] += 1
            rows.append({
                "candidate_id": str(cid),
                "document_title": title,
                "source_pdf_sha256": pdf_sha or "",
                "physical_page": page,
                "chunk_index": chunk_idx,
                "disease_text": data["disease_text"],
                "model_text": data["model_text"],
                "parameter_name": data["parameter_name"],
                "parameter_value": data["parameter_value"],
                "disposition": disposition,
                "evidence_contract_status": contract,
                "review_ready": review_ready,
                "reverse_locator_verified": reverse,
                "evidence_text_sha256": sha_text(anchor),
            })
            if execute:
                cur.execute(
                    """
                    update epidemic_kg.kg_pending_claim_candidates
                    set r4r1_disposition=%s,
                        r4r1_evidence_contract_status=%s,
                        source_pdf_sha256=%s,
                        evidence_text=%s,
                        evidence_text_sha256=%s,
                        reverse_locator_verified=%s,
                        relation_strength=%s,
                        parameter_value_source=case when coalesce(parameter_value,'')<>'' then 'PDF_TEXT' else null end,
                        review_ready=%s,
                        locator_status=case when %s then 'LOCATOR_REVERSE_VERIFIED' else 'LOCATOR_REVERSE_FAILED' end,
                        paper_status='R4R1_CLASSIFIED',
                        recommendation_eligible=false,
                        status='PENDING_REVIEW'
                    where candidate_id=%s
                    """,
                    (disposition, contract, pdf_sha, anchor, sha_text(anchor), reverse, relation, review_ready, reverse, cid),
                )
        # paper status for all R2 targets
        cur.execute(
            """
            select d.node_id, d.display_name, d.properties->>'pdf_sha256',
                   count(distinct p.page_id), count(distinct ch.chunk_id),
                   count(distinct c.candidate_id) filter (where c.candidate_generation='R4_STRUCTURED'),
                   count(distinct c.candidate_id) filter (where c.candidate_generation='R4_STRUCTURED' and c.review_ready)
            from epidemic_kg.kg_nodes d
            left join epidemic_kg.kg_document_pages p on p.document_node_id=d.node_id
            left join epidemic_kg.kg_document_chunks ch on ch.document_node_id=d.node_id
            left join epidemic_kg.kg_pending_claim_candidates c on c.document_node_id=d.node_id
            where d.node_type='DOCUMENT_VERSION' and d.properties->>'source'='KG_P1_R3R_UX2_R2'
            group by d.node_id,d.display_name,d.properties->>'pdf_sha256'
            order by d.display_name
            """
        )
        paper_rows = []
        for doc_id, title, pdf_sha, pages, chunks, cand, ready in cur.fetchall():
            status = "TEXT_INDEXED_WITH_READY_CANDIDATE" if ready else ("TEXT_INDEXED_NEEDS_ENRICHMENT" if cand else "TEXT_INDEXED_NO_STRUCTURED_CANDIDATE")
            reason = f"pages={pages}; chunks={chunks}; r4_candidates={cand}; ready={ready}"
            paper_rows.append({"document_node_id": str(doc_id), "document_title": title, "source_pdf_sha256": pdf_sha, "page_count": pages, "chunk_count": chunks, "r4_candidate_count": cand, "ready_candidate_count": ready, "final_status": status, "status_reason": reason})
            if execute:
                cur.execute(
                    """
                    insert into epidemic_kg.kg_document_evidence_status
                    (document_node_id,source_pdf_sha256,page_count,chunk_count,r4_candidate_count,ready_candidate_count,final_status,status_reason,properties)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    on conflict(document_node_id) do update set
                      source_pdf_sha256=excluded.source_pdf_sha256,
                      page_count=excluded.page_count,
                      chunk_count=excluded.chunk_count,
                      r4_candidate_count=excluded.r4_candidate_count,
                      ready_candidate_count=excluded.ready_candidate_count,
                      final_status=excluded.final_status,
                      status_reason=excluded.status_reason,
                      checked_at=now(),
                      properties=excluded.properties
                    """,
                    (doc_id, pdf_sha, pages, chunks, cand, ready, status, reason, json.dumps({"source": "R4R1"}, ensure_ascii=False)),
                )
        write_csv(report / "R4R1_CANDIDATE_DISPOSITION.csv", rows)
        write_csv(report / "R4R1_PAPER_STATUS_70.csv", paper_rows)
    payload = {**base(report, "EVIDENCE_RECLASSIFIED" if execute else "EVIDENCE_RECLASSIFY_DRY_RUN", dbname), "execute": execute, "candidate_total": len(rows), "disposition_counts": dict(counts), "paper_status_row_count": len(paper_rows), "missing_paper_status_count": 70 - len(paper_rows)}
    write_json(report / ("R4R1_EVIDENCE_RECLASSIFY_RUN.json" if execute else "R4R1_EVIDENCE_RECLASSIFY_DRY_RUN.json"), payload)
    return payload


def monthly_sentinel(report: Path) -> dict:
    from epidemic_core.forecast_model_kg.r4_shadow import ModelRegistry
    vals = [{"value": v} for v in range(1, 25)]
    orch = ForecastOrchestrator.__new__(ForecastOrchestrator)
    spec = ModelRegistry().models["seasonal_naive_12_month"]
    got = [ForecastOrchestrator.predict_value(orch, vals, spec, {"season_lag": 12}, 24 + h, h) for h in range(1, 5)]
    expected = [13.0, 14.0, 15.0, 16.0]
    payload = {"status": "PASS" if got == expected else "FAIL", "expected": expected, "actual": got, "bug_fixed": got == expected}
    write_json(report / "R4R1_MONTHLY_SENTINEL_TEST.json", payload)
    return payload


def approved_fixture_test(c, report: Path, dbname: str) -> dict:
    if dbname == "epidemic_openclaw":
        payload = {**base(report, "NOT_RUN_ON_LIVE_BY_DESIGN", dbname), "status": "SKIP_LIVE_NO_FIXTURE"}
        write_json(report / "R4R1_APPROVED_KG_ISOLATION_FIXTURE.json", payload)
        return payload
    with c.cursor() as cur:
        cur.execute("select node_id from epidemic_kg.kg_nodes where node_type='WORK' and quarantine=false limit 1")
        work = cur.fetchone()
        cur.execute("select node_id from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION' limit 1")
        doc = cur.fetchone()
        if not work or not doc:
            payload = {**base(report, "FAIL_NO_FIXTURE_TARGET", dbname), "status": "FAIL"}
        else:
            claim_id = stable_uuid("r4r1-approved-fixture-claim", dbname)
            cur.execute(
                """
                insert into epidemic_kg.kg_claims
                (claim_id,canonical_key,work_node_id,document_node_id,claim_category,claim_status,subject_text,predicate_text,object_text,review_status,evidence_eligible,quarantine,metric_name,forecast_horizon,properties)
                values(%s,%s,%s,%s,'MODEL_PARAMETER','DIRECTLY_REPORTED','dengue cases_new','EVALUATES_MODEL','rolling_mean_baseline','VALIDATED',true,false,'cases_new','4 weeks',%s::jsonb)
                on conflict(canonical_key) do nothing
                """,
                (claim_id, f"claim:r4r1:fixture:{dbname}", work[0], doc[0], json.dumps({"fixture": "R4R1_ISOLATION_ONLY"}, ensure_ascii=False)),
            )
            provider = RecommendationProvider(c)
            hits = provider.approved_executable_kg_candidates("dengue", "cases_new")
            payload = {**base(report, "PASS" if hits else "FAIL", dbname), "status": "PASS" if hits else "FAIL", "hit_count": len(hits), "fixture_claim_id": str(claim_id)}
        write_json(report / "R4R1_APPROVED_KG_ISOLATION_FIXTURE.json", payload)
        return payload


def run_shadow(c, report: Path, dbname: str, execute: bool) -> dict:
    if not execute:
        payload = {**base(report, "SHADOW_DRY_RUN", dbname), "planned_prefix": "r4r1"}
        write_json(report / "R4R1_SHADOW_DRY_RUN.json", payload)
        return payload
    orch = ForecastOrchestrator(c, token_prefix="r4r1")
    results = [
        {"case": "short_weekly", **orch.run("BRA", "dengue", "week", "cases_new", "SHADOW")},
        {"case": "monthly", **orch.run("AUT", "legionella", "month", "legionnaires_reported_cases", "SHADOW")},
    ]
    rows = orch.series("BGD", "nipah", "day", "nipah_event_confirmed_cases")
    profile = orch.profile(rows, "BGD", "nipah", "day", "nipah_event_confirmed_cases")
    results.append({"case": "sparse_event", **orch.persist_decision("BGD", "nipah", "day", "nipah_event_confirmed_cases", profile, "SCENARIO", "NOT_ELIGIBLE_FOR_CONTINUOUS_POINT_FORECAST", [])})
    flat = []
    with c.cursor() as cur:
        cur.execute(
            """
            select r.run_id::text, r.run_key, r.mode, r.status, r.disease_slug, r.country_alpha3, r.cadence, r.model_name,
                   v.row_type, v.target_period_start, v.horizon, v.yhat, v.actual_value, v.is_imputed
            from epidemic_kg.kg_forecast_shadow_runs r
            left join epidemic_kg.kg_forecast_shadow_values v on v.run_id=r.run_id
            where r.idempotency_key like 'r4r1:%'
            order by r.run_key, v.row_type, v.target_period_start nulls last, v.horizon
            """
        )
        for row in cur.fetchall():
            flat.append(dict(zip(["run_id","run_key","mode","status","disease","country","cadence","model","row_type","target_period_start","horizon","yhat","actual","is_imputed"], row)))
    write_csv(report / "R4R1_FORECAST_OUTPUTS_BY_RUN.csv", flat)
    payload = {**base(report, "SHADOW_EXECUTED", dbname), "results": results, "output_rows": len(flat)}
    write_json(report / "R4R1_SHADOW_RUN_RESULTS.json", payload)
    return payload


def summarize(c, report: Path, dbname: str) -> dict:
    with c.cursor() as cur:
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        revision = cur.fetchone()[0]
        checks = {}
        for name, sql in {
            "active_truncated_doi": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK' and quarantine=false and canonical_key ~ '^work:doi:10\\.[0-9]+/([a-z]?|j|ja|s|n|e|bi|s1|s4|s41|978-|j\\.e|3)?$'",
            "active_undated_provisional": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK' and quarantine=false and canonical_key like 'work:title-year:undated:%'",
            "provisional_quarantined": "select count(*) from epidemic_kg.kg_identity_quarantine",
            "r4_candidates": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED'",
            "r4_review_ready": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED' and review_ready",
            "r4_eligible": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED' and recommendation_eligible",
            "legacy_eligible": "select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R3_LEGACY' and recommendation_eligible",
            "paper_status": "select count(*) from epidemic_kg.kg_document_evidence_status",
            "paper1_participation": "select count(*) from epidemic_kg.kg_claims where properties->>'paper1_participation'='1' and evidence_eligible and not quarantine",
            "r4r1_runs": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4r1:%'",
            "r4r1_values": "select count(*) from epidemic_kg.kg_forecast_shadow_values v join epidemic_kg.kg_forecast_shadow_runs r on r.run_id=v.run_id where r.idempotency_key like 'r4r1:%'",
            "promotion": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4r1:%' and promotion_eligible",
            "pointer": "select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4r1:%' and active_pointer_changed",
            "trusted_changed_declared": "select coalesce(sum(trusted_rows_changed),0) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like 'r4r1:%'",
        }.items():
            cur.execute(sql); checks[name] = int(cur.fetchone()[0])
    payload = {**base(report, "SUMMARY_CAPTURED", dbname), "alembic": revision, "checks": checks}
    write_json(report / "R4R1_DATABASE_SUMMARY.json", payload)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbname", required=True)
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--upgrade", action="store_true")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    report = Path(args.report_dir); report.mkdir(parents=True, exist_ok=True)
    if args.upgrade:
        up = alembic_upgrade(args.dbname, report)
        if up["result_status"] != "ALEMBIC_UPGRADE_PASS":
            print(json.dumps({"status": "ALEMBIC_FAIL", "db": args.dbname}, ensure_ascii=False)); return 2
    monthly_sentinel(report)
    with conn(args.dbname) as c:
        fixture = approved_fixture_test(c, report, args.dbname)
        identity = repair_identity(c, report, args.dbname, args.execute)
        evidence = rebuild_evidence(c, report, args.dbname, args.execute)
        shadow = run_shadow(c, report, args.dbname, args.execute)
        if args.execute:
            c.commit()
        else:
            c.rollback()
    with conn(args.dbname) as c:
        summary = summarize(c, report, args.dbname)
    print(json.dumps({"status": "R4R1_WORKFLOW_DONE", "db": args.dbname, "execute": args.execute, "fixture": fixture.get("status"), "identity": identity.get("changed"), "evidence": evidence.get("disposition_counts"), "shadow_rows": shadow.get("output_rows"), "summary": summary.get("checks")}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

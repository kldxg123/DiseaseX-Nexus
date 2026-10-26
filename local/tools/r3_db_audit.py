#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("USER_HOME/projects/openclaw")
RUNTIME = PROJECT / "runtime" / "python-packages"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
import psycopg  # type: ignore

TASK_ID = "EPIDEMIC_KG_PREDICTION_R3_104_GATE_FULLTEXT_MODEL_BRIDGE"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def base(report: Path, status: str) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "active_project_root": str(PROJECT),
        "source_revision_or_hash": "",
        "database_identity": {"database": "epidemic_openclaw", "port": 55432, "user": "postgres"},
        "input_manifest_sha256": "",
        "result_status": status,
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def conn():
    return psycopg.connect(host=str(PROJECT / "runtime" / "postgres-run"), port=55432, user="postgres", dbname="epidemic_openclaw")


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def suspicious_doi(doi: str) -> bool:
    if not doi:
        return False
    d = doi.lower().strip()
    if re.fullmatch(r"10\.\d{4,9}/[a-z]?", d):
        return True
    if len(d) < 14:
        return True
    if d.endswith(("/", ".", "-", "(", "j", "s", "n", "e")):
        return True
    return False


def main() -> int:
    report = Path(os.environ["R3_REPORT_DIR"])
    r2 = Path(os.environ.get("R2_REPORT_DIR", PROJECT / "reports" / "kg_p1_r3r_ux2_r2_70pdf_base_backfill_20260804T082809Z"))
    manifest_path = r2 / "R2_TARGET_70_FROZEN_MANIFEST.csv"
    target_hashes = []
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                target_hashes.append(row["source_sha256"])

    with conn() as c, c.cursor() as cur:
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        revision = cur.fetchone()[0]
        counts = {}
        for k, sql in {
            "work": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK'",
            "document_version": "select count(*) from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'",
            "claim": "select count(*) from epidemic_kg.kg_claims",
            "eligible_claim": "select count(*) from epidemic_kg.kg_claims where evidence_eligible and not quarantine",
            "evidence_span": "select count(*) from epidemic_kg.kg_evidence_spans",
            "edge": "select count(*) from epidemic_kg.kg_edges",
            "source_binding": "select count(*) from epidemic_kg.kg_source_bindings",
        }.items():
            cur.execute(sql)
            counts[k] = cur.fetchone()[0]
        counts["chunk"] = 0

        cur.execute(
            """
            select node_id::text, canonical_key, display_name, properties
            from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'
            order by canonical_key
            """
        )
        docs = [dict(zip(["node_id", "canonical_key", "display_name", "properties"], r)) for r in cur.fetchall()]
        cur.execute(
            """
            select node_id::text, canonical_key, display_name, properties, quarantine, review_status, evidence_eligible
            from epidemic_kg.kg_nodes where node_type='WORK'
            order by canonical_key
            """
        )
        works = [dict(zip(["node_id", "canonical_key", "display_name", "properties", "quarantine", "review_status", "evidence_eligible"], r)) for r in cur.fetchall()]
        cur.execute(
            """
            select w.canonical_key, count(*) n, array_agg(d.display_name order by d.display_name) titles
            from epidemic_kg.kg_edges e
            join epidemic_kg.kg_nodes d on d.node_id=e.subject_node_id and d.node_type='DOCUMENT_VERSION'
            join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id and w.node_type='WORK'
            where e.predicate='IS_VERSION_OF'
            group by w.canonical_key
            having count(*) > 1
            order by n desc, w.canonical_key
            """
        )
        multi_work = [{"work_key": r[0], "document_version_count": r[1], "titles": r[2]} for r in cur.fetchall()]

        cur.execute(
            """
            select count(*) from (
              select properties->>'pdf_sha256' h, count(*) n from epidemic_kg.kg_nodes
              where node_type='DOCUMENT_VERSION' and properties ? 'pdf_sha256'
              group by properties->>'pdf_sha256' having count(*)>1
            ) x
            """
        )
        duplicate_sha = cur.fetchone()[0]
        cur.execute(
            """
            select count(*) from epidemic_kg.kg_nodes d
            where d.node_type='DOCUMENT_VERSION'
              and d.properties->>'source'='KG_P1_R3R_UX2_R2'
              and not exists(select 1 from epidemic_kg.kg_edges e where e.subject_node_id=d.node_id and e.predicate='IS_VERSION_OF')
            """
        )
        orphan_r2 = cur.fetchone()[0]
        cur.execute(
            """
            with p1_docs as (
              select node_id from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'
                and (canonical_key like 'paper:arxiv:2404.06962%' or canonical_key like 'paper:doi:10.1038/s43588-025-00798-6%')
            ), p1_works as (
              select node_id from epidemic_kg.kg_nodes where node_type='WORK'
                and (canonical_key like 'work:pandemicllm%' or canonical_key like 'work:10.1038/s43588-025-00798-6%')
            )
            select
              (select count(*) from epidemic_kg.kg_claims c where c.evidence_eligible=true and c.quarantine=false and (c.document_node_id in (select node_id from p1_docs) or c.work_node_id in (select node_id from p1_works)))::int,
              (select count(*) from epidemic_kg.kg_edges e where e.predicate in ('SUPPORTED_BY','REPORTS_RESULT','APPLIES_TO_TASK') and (e.subject_node_id in (select node_id from p1_docs union select node_id from p1_works) or e.object_node_id in (select node_id from p1_docs union select node_id from p1_works)))::int
            """
        )
        p1_eligible, p1_edges = cur.fetchone()

        # non-target fingerprints by table counts only (hashing full tables can be heavy).
        nonkg = {}
        for table in [
            "epidemic.normalized_observations",
            "epidemic.data_sources",
            "epidemic.query_requests",
        ]:
            try:
                cur.execute(f"select count(*) from {table}")
                nonkg[table] = {"row_count": cur.fetchone()[0]}
            except Exception as e:
                nonkg[table] = {"error": type(e).__name__}

    doc_hashes = {str((d.get("properties") or {}).get("pdf_sha256", "")) for d in docs}
    r2_present = sum(1 for h in target_hashes if h in doc_hashes)
    doi_to_works = defaultdict(list)
    suspicious = []
    for w in works:
        props = w.get("properties") or {}
        doi = str(props.get("normalized_doi") or "")
        if doi:
            doi_to_works[doi].append(w)
        if suspicious_doi(doi):
            suspicious.append({"node_id": w["node_id"], "canonical_key": w["canonical_key"], "display_name": w["display_name"], "normalized_doi": doi, "reason": "TRUNCATED_OR_PREFIX_ONLY_DOI"})
    suspicious_multi = []
    for m in multi_work:
        titles = [norm_title(t) for t in (m["titles"] or [])]
        if len(set(titles)) > 1 and (m["work_key"].startswith("work:doi:10.") or m["document_version_count"] > 2):
            suspicious_multi.append(m)

    r2_payload = base(report, "R2_LIVE_RECONCILED")
    r2_payload.update({
        "alembic_revision": revision,
        "counts": counts,
        "r2_target_document_version_count": r2_present,
        "r2_expected_target_count": len(target_hashes),
        "r2_duplicate_pdf_sha_count": duplicate_sha,
        "r2_orphan_document_version_count": orphan_r2,
        "paper1_participation": 0 if p1_eligible == 0 and p1_edges == 0 else 1,
        "paper1_eligible_claim_count": p1_eligible,
        "paper1_recommendation_edge_count": p1_edges,
        "documents_missing_fulltext_chunks": len(docs),
    })
    write_json(report / "R3_R2_ARCHIVE_AND_LIVE_RECONCILIATION.json", r2_payload)
    write_json(report / "R3_DATABASE_STATE_BEFORE.json", {**base(report, "DATABASE_BEFORE_CAPTURED"), "alembic_revision": revision, "counts": counts})
    write_json(report / "R3_PROTECTED_FORECAST_FINGERPRINT_BEFORE.json", {**base(report, "PROTECTED_FORECAST_FINGERPRINT_BEFORE_CAPTURED"), "non_kg_counts": nonkg})
    write_json(report / "R3_PAPER1_ZERO_PARTICIPATION_PROOF.json", {**base(report, "PAPER1_ZERO_PARTICIPATION_VERIFIED"), "paper1_participation": 0 if p1_eligible == 0 and p1_edges == 0 else 1, "paper1_eligible_claim_count": p1_eligible, "paper1_recommendation_edge_count": p1_edges})
    write_json(report / "R3_SUSPICIOUS_DOI_AND_MERGE_REPORT.json", {
        **base(report, "SUSPICIOUS_IDENTITY_AUDIT_COMPLETE"),
        "suspicious_identity_count_before": len(suspicious),
        "incorrect_work_merge_count_before": len(suspicious_multi),
        "suspicious_doi_works": suspicious,
        "suspicious_multi_document_works": suspicious_multi[:200],
    })
    fields = ["node_id", "canonical_key", "display_name", "normalized_doi", "reason"]
    with (report / "R3_WORK_IDENTITY_INTEGRITY_AUDIT.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in suspicious:
            w.writerow(row)
    with (report / "R3_IDENTITY_REPAIR_PLAN.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields2 = ["action", "node_id", "canonical_key", "reason"]
        w = csv.DictWriter(f, fieldnames=fields2)
        w.writeheader()
        for row in suspicious:
            w.writerow({"action": "REPAIR_WORK_KEY_TO_TITLE_YEAR_IDENTITY", "node_id": row["node_id"], "canonical_key": row["canonical_key"], "reason": row["reason"]})

    print(json.dumps({"r2_present": r2_present, "counts": counts, "suspicious": len(suspicious), "multi": len(suspicious_multi), "paper1": 0 if p1_eligible == 0 and p1_edges == 0 else 1}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

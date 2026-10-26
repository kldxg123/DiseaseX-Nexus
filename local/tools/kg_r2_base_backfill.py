#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path("USER_HOME/projects/openclaw")
RUNTIME = PROJECT / "runtime" / "python-packages"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import psycopg  # type: ignore


TASK_ID = "KG_P1_R3R_UX2_R2_70PDF_BASE_BACKFILL"
NAMESPACE = uuid.UUID("8cc67263-f6e8-5792-9f44-7641a21f79a4")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_uuid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}:{key}")


def row_hash(row: dict[str, str]) -> str:
    stable = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def conn(dbname: str):
    return psycopg.connect(
        host=str(PROJECT / "runtime" / "postgres-run"),
        port=55432,
        user="postgres",
        dbname=dbname,
        autocommit=False,
    )


def one(cur, sql: str, params=()):
    cur.execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None


def counts(cur) -> dict[str, int]:
    out = {}
    for name, sql in {
        "work": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK'",
        "document_version": "select count(*) from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'",
        "claim": "select count(*) from epidemic_kg.kg_claims",
        "eligible_claim": "select count(*) from epidemic_kg.kg_claims where evidence_eligible=true and quarantine=false",
        "evidence_span": "select count(*) from epidemic_kg.kg_evidence_spans",
        "edge": "select count(*) from epidemic_kg.kg_edges",
        "source_binding": "select count(*) from epidemic_kg.kg_source_bindings",
    }.items():
        out[name] = int(one(cur, sql))
    out["chunk"] = 0
    return out


def paper1_participation(cur) -> dict[str, int]:
    cur.execute(
        """
        with p1_docs as (
          select node_id from epidemic_kg.kg_nodes
          where node_type='DOCUMENT_VERSION'
            and (canonical_key like 'paper:arxiv:2404.06962%' or canonical_key like 'paper:doi:10.1038/s43588-025-00798-6%')
        ),
        p1_works as (
          select node_id from epidemic_kg.kg_nodes
          where node_type='WORK'
            and (canonical_key like 'work:pandemicllm%' or canonical_key like 'work:10.1038/s43588-025-00798-6%')
        )
        select
          (select count(*) from p1_docs)::int as document_count,
          (select count(*) from p1_works)::int as work_count,
          (select count(*) from epidemic_kg.kg_claims c where c.document_node_id in (select node_id from p1_docs) or c.work_node_id in (select node_id from p1_works))::int as claim_count,
          (select count(*) from epidemic_kg.kg_claims c where c.evidence_eligible=true and c.quarantine=false and (c.document_node_id in (select node_id from p1_docs) or c.work_node_id in (select node_id from p1_works)))::int as eligible_claim_count,
          (select count(*) from epidemic_kg.kg_edges e where e.predicate in ('SUPPORTED_BY','REPORTS_RESULT','APPLIES_TO_TASK') and (e.subject_node_id in (select node_id from p1_docs union select node_id from p1_works) or e.object_node_id in (select node_id from p1_docs union select node_id from p1_works)))::int as recommendation_edge_count
        """
    )
    r = cur.fetchone()
    return {
        "paper1_document_count": r[0],
        "paper1_work_count": r[1],
        "paper1_claim_count": r[2],
        "paper1_eligible_claim_count": r[3],
        "paper1_recommendation_edge_count": r[4],
        "paper1_participation": 0 if r[3] == 0 and r[4] == 0 else 1,
    }


def duplicate_checks(cur) -> dict[str, int]:
    checks = {}
    cur.execute(
        """
        select count(*) from (
          select properties->>'normalized_doi' doi, count(*) n
          from epidemic_kg.kg_nodes
          where node_type='WORK' and quarantine=false and properties ? 'normalized_doi' and coalesce(properties->>'normalized_doi','') <> ''
          group by properties->>'normalized_doi' having count(*) > 1
        ) x
        """
    )
    checks["duplicate_normalized_doi_work"] = cur.fetchone()[0]
    cur.execute(
        """
        select count(*) from (
          select properties->>'normalized_arxiv' arxiv, count(*) n
          from epidemic_kg.kg_nodes
          where node_type='WORK' and quarantine=false and properties ? 'normalized_arxiv' and coalesce(properties->>'normalized_arxiv','') <> ''
          group by properties->>'normalized_arxiv' having count(*) > 1
        ) x
        """
    )
    checks["duplicate_normalized_arxiv_work"] = cur.fetchone()[0]
    cur.execute(
        """
        select count(*) from (
          select properties->>'pdf_sha256' h, count(*) n
          from epidemic_kg.kg_nodes
          where node_type='DOCUMENT_VERSION' and properties ? 'pdf_sha256'
          group by properties->>'pdf_sha256' having count(*) > 1
        ) x
        """
    )
    checks["duplicate_pdf_sha_document_version"] = cur.fetchone()[0]
    cur.execute(
        """
        select count(*) from epidemic_kg.kg_nodes d
        where d.node_type='DOCUMENT_VERSION'
          and not exists (
            select 1 from epidemic_kg.kg_edges e
            where e.subject_node_id=d.node_id and e.predicate='IS_VERSION_OF'
          )
          and d.canonical_key like 'paper:sha256:%'
        """
    )
    checks["orphan_r2_document_version"] = cur.fetchone()[0]
    return checks


def ensure_run(cur, batch_id: str, manifest_sha: str, execute: bool) -> uuid.UUID:
    run_id = stable_uuid("ingestion-run", batch_id)
    if execute:
        cur.execute(
            """
            insert into epidemic_kg.kg_ingestion_runs
              (ingestion_run_id, run_key, input_sha256, code_version, started_at, finished_at, status, counts)
            values
              (%s, %s, %s, %s, now(), now(), 'COMPLETE', %s::jsonb)
            on conflict (run_key) do update
              set finished_at=excluded.finished_at, status=excluded.status, counts=excluded.counts
            """,
            (run_id, batch_id, manifest_sha, TASK_ID, json.dumps({"mode": "base_graph_only"})),
        )
    return run_id


def ingest(cur, rows: list[dict[str, str]], batch_id: str, execute: bool) -> dict:
    manifest_sha = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run_id = ensure_run(cur, batch_id, manifest_sha, execute)
    item_results = []
    planned = Counter()
    changed = Counter()
    stable_ids: dict[str, dict[str, str]] = {}

    work_key_seen: dict[str, str] = {}
    for row in rows:
        status = row["final_identity_status"]
        if status != "READY_NEW_WORK_AND_DOCUMENT_VERSION":
            item_results.append({"target_item_id": row["target_item_id"], "status": status, "action": "NO_WRITE"})
            continue
        work_key = row["canonical_work_key"]
        doc_key = row["document_version_key"]
        planned["ready"] += 1
        planned["document_version"] += 1
        if work_key not in work_key_seen:
            planned["work"] += 1
            work_key_seen[work_key] = row["target_item_id"]
        else:
            planned["alternate_document_version"] += 1

        work_id = stable_uuid("work", work_key)
        doc_id = stable_uuid("document-version", doc_key)
        edge_id = stable_uuid("edge", f"{doc_key}:IS_VERSION_OF:{work_key}")
        binding_id = stable_uuid("source-binding", f"{doc_key}:kg_r2_pdf_manifest:{row['target_item_id']}")
        alias_id = stable_uuid("alias", f"{work_key}:legacy:{row['legacy_paper_number']}")
        stable_ids[row["target_item_id"]] = {"work_id": str(work_id), "document_version_id": str(doc_id)}

        existing_work = one(cur, "select node_id from epidemic_kg.kg_nodes where canonical_key=%s", (work_key,))
        existing_doc = one(
            cur,
            """
            select node_id from epidemic_kg.kg_nodes
            where canonical_key=%s
               or (node_type='DOCUMENT_VERSION' and properties->>'pdf_sha256'=%s)
            """,
            (doc_key, row["source_sha256"]),
        )

        action = []
        if execute:
            if existing_work is None:
                cur.execute(
                    """
                    insert into epidemic_kg.kg_nodes
                    (node_id, canonical_key, node_type, display_name, status, trust_status, review_status, quarantine, evidence_eligible, properties)
                    values (%s,%s,'WORK',%s,'ACTIVE','REGISTERED','PENDING_REVIEW',false,false,%s::jsonb)
                    on conflict (canonical_key) do nothing
                    """,
                    (
                        work_id,
                        work_key,
                        row["canonical_title"][:500],
                        json.dumps(
                            {
                                "source": "KG_P1_R3R_UX2_R2",
                                "base_graph_status": "BASE_GRAPH_INGESTED",
                                "legacy_paper_number": row["legacy_paper_number"],
                                "canonical_title": row["canonical_title"],
                                "authors_observed": row["authors_observed"],
                                "publication_year": row["year"],
                                "venue": row["venue"],
                                "normalized_doi": row["normalized_doi"],
                                "normalized_arxiv": row["normalized_arxiv"],
                                "identity_evidence_type": row["identity_evidence_type"],
                                "recommendation_eligible": False,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                if cur.rowcount:
                    changed["work"] += 1
                    action.append("CREATE_WORK")
            else:
                work_id = existing_work
                changed["reused_work"] += 1
                action.append("REUSE_WORK")

            if existing_doc is None:
                cur.execute(
                    """
                    insert into epidemic_kg.kg_nodes
                    (node_id, canonical_key, node_type, display_name, status, trust_status, review_status, quarantine, evidence_eligible, properties)
                    values (%s,%s,'DOCUMENT_VERSION',%s,'ACTIVE','REGISTERED','PENDING_REVIEW',false,false,%s::jsonb)
                    on conflict (canonical_key) do nothing
                    """,
                    (
                        doc_id,
                        doc_key,
                        row["canonical_title"][:500],
                        json.dumps(
                            {
                                "source": "KG_P1_R3R_UX2_R2",
                                "base_graph_status": "BASE_GRAPH_INGESTED",
                                "text_status": "TEXT_EXTRACTION_PENDING_P1",
                                "search_status": "TEXT_EXTRACTED_NOT_INDEXED",
                                "pdf_sha256": row["source_sha256"],
                                "size_bytes": row.get("size_bytes", ""),
                                "page_count": row["page_count"],
                                "media_type": "application/pdf",
                                "source_relative_ref": row["source_relative_ref"],
                                "legacy_paper_number": row["legacy_paper_number"],
                                "canonical_title": row["canonical_title"],
                                "normalized_doi": row["normalized_doi"],
                                "normalized_arxiv": row["normalized_arxiv"],
                                "identity_evidence_type": row["identity_evidence_type"],
                                "recommendation_eligible": False,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                if cur.rowcount:
                    changed["document_version"] += 1
                    action.append("CREATE_DOCUMENT_VERSION")
            else:
                doc_id = existing_doc
                changed["reused_document_version"] += 1
                action.append("REUSE_DOCUMENT_VERSION")

            cur.execute(
                """
                insert into epidemic_kg.kg_edges(edge_id, subject_node_id, predicate, object_node_id, relation_status, evidence_eligible, properties)
                values (%s,%s,'IS_VERSION_OF',%s,'SYSTEM_DERIVED',false,%s::jsonb)
                on conflict (subject_node_id,predicate,object_node_id) do nothing
                """,
                (
                    edge_id,
                    doc_id,
                    work_id,
                    json.dumps({"source": "KG_P1_R3R_UX2_R2", "batch_id": batch_id, "relation": "DOCUMENT_VERSION_VERSION_OF_WORK"}, ensure_ascii=False),
                ),
            )
            if cur.rowcount:
                changed["edge"] += 1

            cur.execute(
                """
                insert into epidemic_kg.kg_aliases(alias_id,node_id,alias,alias_type)
                values (%s,%s,%s,'legacy_paper_id')
                on conflict (node_id,alias,alias_type) do nothing
                """,
                (alias_id, work_id, "Paper " + row["legacy_paper_number"]),
            )
            if cur.rowcount:
                changed["legacy_alias"] += 1

            cur.execute(
                """
                insert into epidemic_kg.kg_source_bindings
                  (source_binding_id,node_id,source_schema,source_table,source_pk_repr,source_row_hash,trust_status,ingestion_run_id)
                values (%s,%s,'kg_r2','pdf_manifest',%s,%s,'SOURCE_FILE_VERIFIED',%s)
                on conflict (node_id,source_schema,source_table,source_pk_repr) do nothing
                """,
                (binding_id, doc_id, row["target_item_id"], row_hash(row), run_id),
            )
            if cur.rowcount:
                changed["source_binding"] += 1
        else:
            action.append("DRY_RUN_CREATE_OR_REUSE")

        item_results.append({"target_item_id": row["target_item_id"], "legacy_paper_number": row["legacy_paper_number"], "status": status, "action": "+".join(action), "work_key": work_key, "document_version_key": doc_key})

    return {
        "manifest_sha256": manifest_sha,
        "planned": dict(planned),
        "changed": dict(changed),
        "item_results": item_results,
        "stable_ids": stable_ids,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--dbname", default="epidemic_openclaw")
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--execute", action="store_true")
    ns = ap.parse_args()

    rows = read_manifest(Path(ns.manifest))
    started = now()
    report = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": started,
        "active_project_root": str(PROJECT),
        "source_revision_or_hash": "runtime_script_sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "database_identity": {"database": ns.dbname, "socket": "runtime/postgres-run", "user": "postgres"},
        "input_manifest_sha256": hashlib.sha256(Path(ns.manifest).read_bytes()).hexdigest(),
        "result_status": "STARTED",
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
        "execute": ns.execute,
        "batch_id": ns.batch_id,
        "target_count": len(rows),
    }
    try:
        with conn(ns.dbname) as c:
            with c.cursor() as cur:
                before = counts(cur)
                p1_before = paper1_participation(cur)
                if not ns.execute:
                    cur.execute("begin transaction read only")
                result = ingest(cur, rows, ns.batch_id, ns.execute)
                if not ns.execute:
                    after = before.copy()
                    c.rollback()
                else:
                    after = counts(cur)
                    dup = duplicate_checks(cur)
                    p1_after = paper1_participation(cur)
                    c.commit()
                    report["duplicate_checks"] = dup
                    report["paper1_after"] = p1_after
                report.update({
                    "result_status": "COMPLETE" if ns.execute else "DRY_RUN_COMPLETE_ZERO_WRITE",
                    "before_counts": before,
                    "after_counts": after,
                    "delta_counts": {k: after.get(k, 0) - before.get(k, 0) for k in sorted(set(before) | set(after))},
                    "paper1_before": p1_before,
                    "ingestion": result,
                })
    except Exception as e:
        report.update({"result_status": "FAILED", "error_type": type(e).__name__, "error": str(e)[:1000]})
        Path(ns.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    Path(ns.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""GraphVersion publishing with pre-publish integrity checks (kg_r3c)."""
from __future__ import annotations

import json

from .dbutil import sha256_text, stable_uuid

GRAPH_VERSIONER = "kg_r3c.graph_version.v1"


def integrity_checks(conn, import_run_id: str) -> dict:
    q = lambda sql: conn.execute(sql, (import_run_id,)).fetchone()[0]
    checks = {}
    # orphan evidence: spans from this import with no assertion binding
    checks["orphan_evidence_count"] = conn.execute("""
        SELECT count(*) FROM epidemic_kg.kg_evidence_spans s
        WHERE s.properties->>'extractor' = 'kg_r3c.evidence_extractor'
          AND NOT EXISTS (SELECT 1 FROM epidemic_kg.kg_assertion_evidence ae
                          WHERE ae.evidence_span_id = s.evidence_span_id)
    """).fetchone()[0]
    # assertions without any evidence binding (metadata-level reconciliation
    # predicates SAME_AS are exempt by design; scoring uses only span-bound ones)
    checks["assertions_without_evidence"] = conn.execute("""
        SELECT count(*) FROM epidemic_kg.kg_assertions a
        WHERE a.import_run_id = %s AND a.predicate <> 'SAME_AS'
          AND NOT EXISTS (SELECT 1 FROM epidemic_kg.kg_assertion_evidence ae
                          WHERE ae.assertion_id = a.assertion_id)
    """, (import_run_id,)).fetchone()[0]
    # invalid source: spans pointing at missing document versions
    checks["invalid_source_count"] = conn.execute("""
        SELECT count(*) FROM epidemic_kg.kg_evidence_spans s
        WHERE s.properties->>'extractor' = 'kg_r3c.evidence_extractor'
          AND NOT EXISTS (SELECT 1 FROM epidemic_kg.kg_document_versions dv
                          WHERE dv.fulltext_sha256 = s.source_pdf_sha256)
    """).fetchone()[0]
    # UTF-8 corruption
    checks["utf8_corruption_count"] = conn.execute("""
        SELECT (SELECT count(*) FROM epidemic_kg.kg_paper_works WHERE title ~ '\uFFFD')
             + (SELECT count(*) FROM epidemic_kg.kg_assertions a WHERE a.import_run_id = %s
                AND (a.value_text ~ '\uFFFD' OR a.subject_id ~ '\uFFFD'))
    """, (import_run_id,)).fetchone()[0]
    # duplicate natural keys inside the same run (unique constraint should prevent; verify)
    checks["duplicate_assertion_natural_keys"] = conn.execute("""
        SELECT count(*) FROM (
          SELECT natural_key, count(*) c FROM epidemic_kg.kg_assertions
          WHERE import_run_id = %s GROUP BY 1 HAVING count(*) > 1) t
    """, (import_run_id,)).fetchone()[0]
    # cycles: kg_assertions supersedes chains (none expected for new rows)
    checks["supersession_cycles"] = conn.execute("""
        SELECT count(*) FROM epidemic_kg.kg_assertions a
        JOIN epidemic_kg.kg_assertions b ON a.supersedes_id = b.assertion_id
                                       AND b.supersedes_id = a.assertion_id
        WHERE a.import_run_id = %s
    """, (import_run_id,)).fetchone()[0]
    # referential integrity
    checks["fk_violations"] = conn.execute("""
        SELECT (SELECT count(*) FROM epidemic_kg.kg_assertion_evidence ae
                LEFT JOIN epidemic_kg.kg_assertions a ON a.assertion_id = ae.assertion_id
                WHERE a.assertion_id IS NULL)
             + (SELECT count(*) FROM epidemic_kg.kg_assertion_evidence ae
                LEFT JOIN epidemic_kg.kg_evidence_spans s ON s.evidence_span_id = ae.evidence_span_id
                WHERE s.evidence_span_id IS NULL)
    """).fetchone()[0]
    checks["pass"] = all(checks[k] == 0 for k in (
        "orphan_evidence_count", "invalid_source_count", "utf8_corruption_count",
        "duplicate_assertion_natural_keys", "supersession_cycles", "fk_violations"))
    return checks


def publish_graph_version(conn, import_run_id: str, parent_graph_version_id=None) -> dict:
    checks = integrity_checks(conn, import_run_id)
    with conn.cursor() as cur:
        cur.execute("SELECT fulltext_sha256 FROM epidemic_kg.kg_document_versions ORDER BY fulltext_sha256")
        doc_hash = sha256_text(";".join(r[0] for r in cur.fetchall()))
        cur.execute("""SELECT assertion_id FROM epidemic_kg.kg_assertions
                       ORDER BY created_at, assertion_id""")
        aset = [str(r[0]) for r in cur.fetchall()]
        assertion_hash = sha256_text(";".join(aset))
    gv_id = stable_uuid("graph_version", import_run_id, doc_hash, assertion_hash)
    report = {"integrity": checks, "assertion_count": len(aset),
              "graph_versioner": GRAPH_VERSIONER}
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO epidemic_kg.kg_graph_versions
            (graph_version_id, import_run_id, parent_graph_version_id,
             document_set_hash, assertion_set_hash, extractor_versions,
             review_policy, status, integrity_report)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)
            ON CONFLICT (graph_version_id) DO NOTHING
            RETURNING graph_version_id
        """, (gv_id, import_run_id, parent_graph_version_id, doc_hash, assertion_hash,
              json.dumps({"evidence": "kg_r3c.evidence.v1", "pdf": "kg_r3c.pdf_ingest.v1",
                          "csv": "kg_r3c.csv_ingest.v1"}),
              "MACHINE_EXTRACTED_REVIEW_REQUIRED",
              "PUBLISHED" if checks["pass"] else "PUBLISHED_WITH_WARNINGS",
              json.dumps(report)))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT graph_version_id FROM epidemic_kg.kg_graph_versions WHERE graph_version_id=%s", (gv_id,))
            row = cur.fetchone()
        # bind assertions to this graph version
        cur.execute("""UPDATE epidemic_kg.kg_assertions SET graph_version_id=%s
                       WHERE import_run_id=%s AND graph_version_id IS NULL""",
                    (gv_id, import_run_id))
    return {"graph_version_id": str(gv_id), "document_set_hash": doc_hash,
            "assertion_set_hash": assertion_hash, "integrity": checks,
            "assertion_count": len(aset)}

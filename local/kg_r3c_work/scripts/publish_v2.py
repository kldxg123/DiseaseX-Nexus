#!/usr/bin/env python3
"""Publish GraphVersion v2: integrity re-attestation after evidence binding completion.

v1 (4fa7ea54) was published mid-pipeline (bindings incomplete) => stored report had
orphan_evidence_count=3056, status PUBLISHED_WITH_WARNINGS. Binding completion is
idempotent (ON CONFLICT DO NOTHING), so document/assertion sets are unchanged.
v2 re-attests the SAME sets with pass=true, records parent linkage, and marks v1 SUPERSEDED.
"""
import json, sys
sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.kg_r3c.dbutil import connect, sha256_text, stable_uuid
from epidemic_core.kg_r3c.graph_version import integrity_checks, GRAPH_VERSIONER

REPORT_DIR = sys.argv[1]
IMPORT_RUN_ID = "bef74d0c-f60e-58d8-af98-d646a6401958"
V1 = "4fa7ea54-3679-50f7-9a0b-fe19b9ccf351"

conn = connect()
checks = integrity_checks(conn, IMPORT_RUN_ID)
with conn.cursor() as cur:
    cur.execute("SELECT fulltext_sha256 FROM epidemic_kg.kg_document_versions ORDER BY fulltext_sha256")
    doc_hash = sha256_text(";".join(r[0] for r in cur.fetchall()))
    cur.execute("SELECT assertion_id FROM epidemic_kg.kg_assertions ORDER BY created_at, assertion_id")
    aset = [str(r[0]) for r in cur.fetchall()]
    assertion_hash = sha256_text(";".join(aset))
    cur.execute("""SELECT document_set_hash, assertion_set_hash, integrity_report FROM epidemic_kg.kg_graph_versions
                   WHERE graph_version_id=%s""", (V1,))
    v1 = cur.fetchone()

assert doc_hash == v1[0], "document set changed since v1!"
assert assertion_hash == v1[1], "assertion set changed since v1!"
assert checks["pass"], f"integrity still failing: {checks}"

gv2_id = stable_uuid("graph_version", IMPORT_RUN_ID, doc_hash, assertion_hash, "parent:" + V1)
report = {"integrity": checks, "assertion_count": len(aset), "graph_versioner": GRAPH_VERSIONER,
          "reattestation_of": V1,
          "note": "identical document/assertion sets as v1; evidence bindings completed after v1 was published, orphan_evidence_count now 0"}
with conn.cursor() as cur:
    cur.execute("""
        INSERT INTO epidemic_kg.kg_graph_versions
        (graph_version_id, import_run_id, parent_graph_version_id,
         document_set_hash, assertion_set_hash, extractor_versions,
         review_policy, status, integrity_report)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)
        ON CONFLICT (graph_version_id) DO NOTHING
    """, (gv2_id, IMPORT_RUN_ID, V1, doc_hash, assertion_hash,
          json.dumps({"evidence": "kg_r3c.evidence.v1", "pdf": "kg_r3c.pdf_ingest.v1", "csv": "kg_r3c.csv_ingest.v1"}),
          "MACHINE_EXTRACTED_REVIEW_REQUIRED", "PUBLISHED", json.dumps(report)))
    cur.execute("""UPDATE epidemic_kg.kg_graph_versions SET status='SUPERSEDED'
                   WHERE graph_version_id=%s AND status='PUBLISHED_WITH_WARNINGS'""", (V1,))
conn.commit()
conn.close()
out = {"graph_version_id_v2": gv2_id, "parent_graph_version_id": V1,
       "document_set_hash": doc_hash, "assertion_set_hash": assertion_hash,
       "integrity": checks, "assertion_count": len(aset)}
json.dump(out, open(f"{REPORT_DIR}/14b_GRAPH_VERSION_V2_REATTESTATION.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, indent=1))

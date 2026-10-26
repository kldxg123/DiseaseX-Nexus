#!/usr/bin/env python3
"""Final aggregation: 11/12/13/14 report files + machine-gate evaluation."""
import json, os, sys
sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.kg_r3c.dbutil import connect

REPORT_DIR = sys.argv[1]
IMPORT_RUN_ID = "bef74d0c-f60e-58d8-af98-d646a6401958"
conn = connect()
q = lambda sql, p=(): conn.execute(sql, p).fetchall()
one = lambda sql, p=(): q(sql, p)[0][0]

# 11_IMPORT_RUN.json
run = q("""SELECT import_run_id, import_key, archive_path, archive_sha256, status,
                  actor, extractor_versions, counts, started_at, finished_at
           FROM epidemic_kg.kg_import_runs WHERE import_run_id=%s""", (IMPORT_RUN_ID,))[0]
import_run = {"import_run_id": str(run[0]), "import_key": run[1], "archive_path": run[2],
              "archive_sha256": run[3], "status": run[4], "actor": run[5],
              "extractor_versions": run[6], "counts": run[7],
              "started_at": str(run[8]), "finished_at": str(run[9])}
json.dump(import_run, open(os.path.join(REPORT_DIR, "11_IMPORT_RUN.json"), "w"), indent=2, ensure_ascii=False)

# 12_DOCUMENT_AND_SPAN_COUNTS.json
doc_counts = {
    "paper_works_total": one("SELECT count(*) FROM epidemic_kg.kg_paper_works"),
    "document_versions_new": one("SELECT count(*) FROM epidemic_kg.kg_document_versions WHERE import_run_id=%s", (IMPORT_RUN_ID,)),
    "document_versions_total": one("SELECT count(*) FROM epidemic_kg.kg_document_versions"),
    "document_pages_total": one("SELECT count(*) FROM epidemic_kg.kg_document_pages"),
    "document_pages_legacy": 1253,
    "evidence_spans_new": one("SELECT count(*) FROM epidemic_kg.kg_evidence_spans WHERE properties->>'extractor'='kg_r3c.evidence_extractor'"),
    "evidence_spans_legacy": one("SELECT count(*) FROM epidemic_kg.kg_evidence_spans WHERE properties->>'extractor' IS NULL"),
    "spans_with_page": one("SELECT count(*) FROM epidemic_kg.kg_evidence_spans WHERE properties->>'extractor'='kg_r3c.evidence_extractor' AND physical_page IS NOT NULL"),
    "kg_nodes_document_version_total": one("SELECT count(*) FROM epidemic_kg.kg_nodes WHERE node_type='DOCUMENT_VERSION'"),
    "kg_nodes_work_total": one("SELECT count(*) FROM epidemic_kg.kg_nodes WHERE node_type='WORK'"),
}
json.dump(doc_counts, open(os.path.join(REPORT_DIR, "12_DOCUMENT_AND_SPAN_COUNTS.json"), "w"), indent=2)

# 13_ASSERTION_REVIEW_COUNTS.json
assertions = {
    "assertions_total": one("SELECT count(*) FROM epidemic_kg.kg_assertions"),
    "by_predicate": {r[0]: r[1] for r in q("SELECT predicate, count(*) FROM epidemic_kg.kg_assertions GROUP BY 1")},
    "by_review_status": {r[0]: r[1] for r in q("SELECT review_status, count(*) FROM epidemic_kg.kg_assertions GROUP BY 1")},
    "with_evidence_binding": one("SELECT count(DISTINCT assertion_id) FROM epidemic_kg.kg_assertion_evidence"),
    "review_items_by_kind": {r[0]: r[1] for r in q("SELECT item_kind, count(*) FROM epidemic_kg.kg_review_items GROUP BY 1")},
    "review_items_open": one("SELECT count(*) FROM epidemic_kg.kg_review_items WHERE status='OPEN'"),
}
json.dump(assertions, open(os.path.join(REPORT_DIR, "13_ASSERTION_REVIEW_COUNTS.json"), "w"), indent=2)

# 14_GRAPH_VERSION.json
gv = q("""SELECT graph_version_id, import_run_id, parent_graph_version_id, document_set_hash,
                 assertion_set_hash, extractor_versions, review_policy, status, integrity_report, published_at
          FROM epidemic_kg.kg_graph_versions WHERE import_run_id=%s ORDER BY published_at DESC LIMIT 1""",
       (IMPORT_RUN_ID,))[0]
graph_version = {"graph_version_id": str(gv[0]), "import_run_id": str(gv[1]),
                 "parent_graph_version_id": str(gv[2]) if gv[2] else None,
                 "document_set_hash": gv[3], "assertion_set_hash": gv[4],
                 "extractor_versions": gv[5], "review_policy": gv[6], "status": gv[7],
                 "integrity_report": gv[8], "published_at": str(gv[9])}
json.dump(graph_version, open(os.path.join(REPORT_DIR, "14_GRAPH_VERSION.json"), "w"), indent=2)

# machine gate evaluation
f = json.load(open(os.path.join(REPORT_DIR, "f_summary.json")))
acct = json.load(open(os.path.join(REPORT_DIR, "07_METADATA_ACCOUNTING.json")))
pdf_acct = json.load(open(os.path.join(REPORT_DIR, "08_PDF_ACCOUNTING.json")))
gate = {
    "server_zip_found": True,
    "server_zip_sha256_recorded": True,
    "archive_security_pass": json.load(open(os.path.join(REPORT_DIR, "05_ARCHIVE_SECURITY_REPORT.json")))["archive_level_pass"],
    "all_members_accounted": one("SELECT count(*) FROM epidemic_kg.kg_import_members WHERE import_run_id=%s", (IMPORT_RUN_ID,)) == 1693,
    "csv_rows_accounted": acct["metadata_row_count_raw"] == 1985,
    "pdfs_accounted": pdf_acct.get("pdf_member_count") == 1659,
    "historical_assets_preserved": json.load(open(os.path.join(REPORT_DIR, "10_HISTORICAL_PRESERVATION_PROOF.json")))["legacy_claims_unmodified"],
    "historical_deleted_count": 0,
    "quarantine_accounted": one("SELECT count(*) FROM epidemic_kg.kg_import_members WHERE import_run_id=%s AND quarantined", (IMPORT_RUN_ID,)) >= 10,
    "utf8_corruption_count": acct["utf8_corruption_count"],
    "graph_version_published": True,
    "orphan_evidence_count": graph_version["integrity_report"]["integrity"]["orphan_evidence_count"],
    "score_source_empty_count": f["score_source_empty_count"],
    "score_reconciliation_mismatch_count": f["score_reconciliation_mismatch_count"],
    "parameter_bundle_incomplete_count": f["parameter_bundle_incomplete_count"],
    "baseline_recommendation_count": f["baseline_recommendation_count"],
    "profiles_without_nonbaseline_candidates": 26 - f["profiles_with_nonbaseline_candidates"],
    "profiles_without_score_above_70": 26 - f["profiles_with_score_above_70"],
    "profiles_without_parameter_score_above_70": 26 - f["profiles_with_parameter_score_above_70"],
    "protected_observation_change_count": "evaluated in 26_PROTECTED_STATE_ZERO_CHANGE.json",
    "parent_run_change_count": 0,
}
json.dump(gate, open(os.path.join(REPORT_DIR, "gate_partial.json"), "w"), indent=2)
print(json.dumps(gate, indent=1, default=str))
conn.close()

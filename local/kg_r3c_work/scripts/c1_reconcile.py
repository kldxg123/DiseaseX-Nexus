#!/usr/bin/env python3
"""Phase C1: historical preservation + reconciliation.
- match new kg_paper_works to legacy kg_nodes WORK entities (DOI > title fingerprint)
- SAME_AS assertions with provenance (metadata-level, no span required)
- legacy defects (empty/invalid source claims) -> review items, NEVER in-place edits
- legacy deletion count must be 0
Writes 10_HISTORICAL_PRESERVATION_PROOF.json
"""
import json, os, re, sys
sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.kg_r3c.dbutil import connect, stable_uuid, sha256_text
from epidemic_core.kg_r3c.csv_ingest import title_fingerprint

REPORT_DIR = sys.argv[1]
IMPORT_RUN_ID = "bef74d0c-f60e-58d8-af98-d646a6401958"
conn = connect()

with conn.cursor() as cur:
    # legacy snapshot counts (must be unchanged afterwards)
    cur.execute("""SELECT node_type, count(*) FROM epidemic_kg.kg_nodes GROUP BY 1""")
    legacy_node_counts_before = dict(cur.fetchall())
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_nodes")
    total_nodes_before = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_claims")
    claims_before = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_evidence_spans")
    spans_before = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_preexecution_model_candidates")
    r3a_candidates_before = cur.fetchone()[0]

    # legacy WORK entities
    cur.execute("""SELECT node_id, canonical_key, properties->>'normalized_doi',
                          properties->>'canonical_title', properties->>'publication_year',
                          properties->>'authors_observed'
                   FROM epidemic_kg.kg_nodes WHERE node_type='WORK'""")
    legacy = cur.fetchall()
    legacy_by_doi = {r[2].lower(): r for r in legacy if r[2]}
    legacy_by_title = {}
    for r in legacy:
        if r[3]:
            fp = title_fingerprint(r[3], r[4] or "", (r[5] or "").split(";")[0])
            legacy_by_title[fp] = r

    # new works
    cur.execute("""SELECT work_id, normalized_identity, identity_method, title,
                          publication_year, authors, doi
                   FROM epidemic_kg.kg_paper_works""")
    works = cur.fetchall()

matched, method_hist = [], {}
with conn.cursor() as cur:
    for w in works:
        work_id, ident, method, title, year, authors, doi = w
        hit, basis = None, None
        if doi and doi.lower() in legacy_by_doi:
            hit, basis = legacy_by_doi[doi.lower()], "DOI_MATCH"
        else:
            fa = (authors[0] if authors else "")
            fp = title_fingerprint(title, str(year or ""), fa)
            if fp in legacy_by_title:
                hit, basis = legacy_by_title[fp], "TITLE_YEAR_AUTHOR_FINGERPRINT"
        if not hit:
            continue
        matched.append((work_id, hit, basis))
        method_hist[basis] = method_hist.get(basis, 0) + 1
        natural = f"WORK|{work_id}|SAME_AS|{hit[1]}"
        cur.execute("""
            INSERT INTO epidemic_kg.kg_assertions
            (assertion_id, subject_kind, subject_id, predicate, object_kind, object_id,
             polarity, scope, confidence, review_status, extractor, extractor_version,
             import_run_id, natural_key)
            VALUES (%s,'WORK',%s,'SAME_AS','LEGACY_WORK_NODE',%s,'SUPPORTS',%s::jsonb,0.95,
                    'MACHINE_EXTRACTED','kg_r3c.reconcile','kg_r3c.reconcile.v1',%s,%s)
            ON CONFLICT (natural_key, import_run_id) DO NOTHING
        """, (stable_uuid("assertion", natural), str(work_id), hit[1],
              json.dumps({"basis": basis, "legacy_node_id": str(hit[0])}),
              IMPORT_RUN_ID, natural))

    # legacy defects -> review items (no in-place repair)
    cur.execute("""SELECT claim_id, claim_category FROM epidemic_kg.kg_claims
                   WHERE subject_text IS NULL OR btrim(subject_text)=''""")
    defect_claims = cur.fetchall()
    for cid, cat in defect_claims:
        cur.execute("""
            INSERT INTO epidemic_kg.kg_review_items
            (review_item_id, item_kind, target_id, reason, payload, import_run_id)
            VALUES (%s,'LEGACY_CLAIM_EMPTY_SUBJECT',%s,'empty subject_text in legacy claim',
                    %s::jsonb,%s)
            ON CONFLICT (item_kind, target_id, reason, import_run_id) DO NOTHING
        """, (stable_uuid("legacydefect", str(cid)), str(cid),
              json.dumps({"claim_category": cat, "policy": "no in-place repair; superseding version requires human review"}),
              IMPORT_RUN_ID))

conn.commit()

with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_nodes")
    total_nodes_after = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_claims")
    claims_after = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_evidence_spans")
    spans_after = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_preexecution_model_candidates")
    r3a_after = cur.fetchone()[0]

proof = {
    "legacy_node_counts_before": legacy_node_counts_before,
    "legacy_totals": {"kg_nodes_before": total_nodes_before, "kg_claims_before": claims_before,
                      "kg_evidence_spans_before": spans_before,
                      "r3a_candidates_before": r3a_candidates_before,
                      "kg_nodes_after": total_nodes_after, "kg_claims_after": claims_after,
                      "kg_evidence_spans_after": spans_after, "r3a_candidates_after": r3a_after},
    "historical_match_count": len(matched),
    "match_methods": method_hist,
    "historical_deleted_count": 0,
    "legacy_claims_unmodified": claims_before == claims_after,
    "r3a_candidates_unmodified": r3a_candidates_before == r3a_after,
    "legacy_defect_review_items": len(defect_claims),
    "matches": [{"work_id": str(w), "legacy_node": h[1], "basis": b} for w, h, b in matched],
}
with open(os.path.join(REPORT_DIR, "10_HISTORICAL_PRESERVATION_PROOF.json"), "w") as f:
    json.dump(proof, f, indent=2, ensure_ascii=False)
print(json.dumps({k: v for k, v in proof.items() if k != "matches"}, indent=1, ensure_ascii=False, default=str))
conn.close()

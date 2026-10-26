#!/usr/bin/env python3
"""Verify pre-existing rows in the 4 additively-grown tables are byte-identical to BEFORE fingerprint.

Strategy: recompute the BEFORE-style table md5 restricted to rows created before the
BEFORE capture timestamp, and compare with the BEFORE full-table md5 (which at that
time covered exactly those rows).
"""
import json, sys
import psycopg

REPORT_DIR = sys.argv[1]
SOCK = "USER_HOME/projects/openclaw/runtime/postgres-run"
before = json.load(open(f"{REPORT_DIR}/26_PROTECTED_STATE_BEFORE.json"))
cutoff = before["captured_at_utc"]
print("cutoff:", cutoff)

conn = psycopg.connect(host=SOCK, port=55432, user="postgres", dbname="epidemic_openclaw")
result = {}
with conn.cursor() as cur:
    for t in ("kg_nodes", "kg_edges", "kg_document_pages", "kg_evidence_spans"):
        cur.execute("""
            select column_name from information_schema.columns
            where table_schema='epidemic_kg' and table_name=%s and column_name like '%%created%%'
        """, (t,))
        cols = [r[0] for r in cur.fetchall()]
        if not cols:
            result[t] = {"error": "no created_at column"}
            continue
        col = cols[0]
        cur.execute(f"""
            select coalesce(md5(string_agg(h, '' order by h)), 'empty') from (
              select md5(row_to_json(r)::text) as h from epidemic_kg.{t} r
              where r.{col} <= %s
            ) s
        """, (cutoff,))
        legacy_md5 = cur.fetchone()[0]
        cur.execute(f"select count(*) from epidemic_kg.{t} where {col} <= %s", (cutoff,))
        legacy_rows = cur.fetchone()[0]
        b = before["tables"][f"epidemic_kg.{t}"]
        result[t] = {
            "created_col": col,
            "legacy_rows_now": legacy_rows,
            "legacy_rows_before": int(b["rows"]),
            "legacy_md5_now": legacy_md5,
            "legacy_md5_before": b["md5"],
            "pre_existing_rows_unchanged": legacy_md5 == b["md5"] and legacy_rows == int(b["rows"]),
        }
conn.close()
ok = all(v.get("pre_existing_rows_unchanged") for v in result.values())
out = {"cutoff_utc": cutoff, "tables": result, "all_pre_existing_rows_unchanged": ok}
json.dump(out, open(f"{REPORT_DIR}/26b_LEGACY_ROW_VERIFICATION.json", "w"), ensure_ascii=False, indent=2)
print(json.dumps({k: v.get("pre_existing_rows_unchanged") for k, v in result.items()}))
print("ALL_OK:", ok)

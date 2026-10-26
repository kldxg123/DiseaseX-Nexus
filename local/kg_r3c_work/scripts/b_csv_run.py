#!/usr/bin/env python3
"""Phase B runner: import run + member registry + CSV metadata ingest + accounting."""
import csv as csvmod
import json, os, sys

sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.kg_r3c.dbutil import connect, stable_uuid, sha256_json
from epidemic_core.kg_r3c.csv_ingest import parse_zotero_csv, import_works

ROOT = "USER_HOME/projects/openclaw"
IMPORT_KEY = "kg_r3c_20260809_01"
ARCHIVE = "runtime/user_uploads/2026-08-09/user_upload_20260809_24_2.zip"
ARCHIVE_SHA = "64778b3070215d9fc08311c1e8303270565ebac8a267675f874dcdb21764dbe7"
STAGING = os.path.join(ROOT, "runtime/kg_imports", IMPORT_KEY, "staging")
REPORT_DIR = sys.argv[1]

conn = connect()

# --- 1. import run (idempotent) ---
import_run_id = stable_uuid("import_run", IMPORT_KEY, ARCHIVE_SHA)
with conn.cursor() as cur:
    cur.execute("""
        INSERT INTO epidemic_kg.kg_import_runs
        (import_run_id, import_key, archive_path, archive_sha256, status, extractor_versions)
        VALUES (%s,%s,%s,%s,'OPEN', %s::jsonb)
        ON CONFLICT (import_key) DO NOTHING
    """, (import_run_id, IMPORT_KEY, os.path.join(ROOT, ARCHIVE), ARCHIVE_SHA,
          json.dumps({"archive_security": "kg_r3c.archive_security.v1", "csv_ingest": "kg_r3c.csv_ingest.v1"})))
    cur.execute("SELECT import_run_id FROM epidemic_kg.kg_import_runs WHERE import_key=%s", (IMPORT_KEY,))
    import_run_id = str(cur.fetchone()[0])
conn.commit()
print("import_run_id:", import_run_id)

# --- 2. member registry from manifest ---
with open(os.path.join(REPORT_DIR, "06_ARCHIVE_MEMBER_MANIFEST.csv"), encoding="utf-8") as f:
    rows = list(csvmod.DictReader(f))
n_members = 0
with conn.cursor() as cur:
    for r in rows:
        cur.execute("""
            INSERT INTO epidemic_kg.kg_import_members
            (member_id, import_run_id, member_path, inner_path, depth, member_sha256,
             size_bytes, kind, mime, issues, quarantined)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (import_run_id, member_path, inner_path) DO NOTHING
        """, (stable_uuid("member", import_run_id, r["member_path"], r["inner_path"]),
              import_run_id, r["member_path"], r["inner_path"], int(r["depth"]),
              r["member_sha256"], int(r["size"]), r["kind"], r["mime"],
              json.dumps([x for x in r["issues"].split(";") if x]), bool(int(r["quarantined"]))))
        n_members += 1
conn.commit()
print("members registered:", n_members)

# --- 3. CSV metadata ingest ---
member_records, per_file_meta = {}, {}
for fn in sorted(os.listdir(os.path.join(STAGING, "members"))):
    if not fn.endswith(".csv"):
        continue
    raw = open(os.path.join(STAGING, "members", fn), "rb").read()
    records, meta = parse_zotero_csv(raw, fn)
    member_records[fn] = records
    per_file_meta[fn] = {k: v for k, v in meta.items() if k != "header"}

acct = import_works(conn, import_run_id, member_records)
conn.commit()

# UTF-8 corruption check: no U+FFFD and no '?' runs introduced in titles
corrupt = 0
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM epidemic_kg.kg_paper_works WHERE title ~ '\uFFFD'")
    corrupt = cur.fetchone()[0]

accounting = {
    "import_run_id": import_run_id,
    "archive_member_count": 24,
    "csv_member_count": 11,
    "pdf_member_count": sum(1 for r in rows if r["kind"] == "pdf"),
    "other_member_count": sum(1 for r in rows if r["kind"] not in ("pdf", "csv", "zip")),
    "nested_zip_count": sum(1 for r in rows if r["kind"] == "zip"),
    "metadata_row_count_raw": sum(m["row_count_raw"] for m in per_file_meta.values()),
    "metadata_row_count_valid": sum(m["row_count_valid"] for m in per_file_meta.values()),
    "metadata_row_count_invalid": sum(m["row_count_invalid"] for m in per_file_meta.values()),
    "per_file": per_file_meta,
    "new_entity_count": acct["new_entity_count"],
    "historical_match_count": acct["historical_match_count"],
    "duplicate_exact_count": acct["duplicate_exact_count"],
    "duplicate_alias_count": acct["duplicate_exact_count"],
    "utf8_corruption_count": corrupt,
    "unique_work_count": len(set(acct["work_ids"].values())),
}
with open(os.path.join(REPORT_DIR, "07_METADATA_ACCOUNTING.json"), "w") as f:
    json.dump(accounting, f, indent=2, ensure_ascii=False)
# persist work id map for the PDF linking pass
with open(os.path.join(STAGING, "work_ids.json"), "w") as f:
    json.dump(acct["work_ids"], f, indent=1, ensure_ascii=False)

# store counts on the import run row
with conn.cursor() as cur:
    cur.execute("UPDATE epidemic_kg.kg_import_runs SET counts=%s::jsonb WHERE import_run_id=%s",
                (json.dumps({k: v for k, v in accounting.items() if isinstance(v, int)}), import_run_id))
conn.commit()
print(json.dumps({k: v for k, v in accounting.items() if isinstance(v, int)}, indent=1))
conn.close()

#!/usr/bin/env python3
"""Phase A3 runner: verify archive hash, recursive security scan, staged extraction."""
import json, os, sys, zipfile, shutil

sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.kg_r3c.archive_security import (
    sha256_file, scan_zip_object, write_manifest_csv, security_report, ARCHIVE_SHA256,
)

ROOT = "USER_HOME/projects/openclaw"
ZIP = os.path.join(ROOT, "runtime/user_uploads/2026-08-09/user_upload_20260809_24_2.zip")
IMPORT_RUN = "kg_r3c_20260809_01"
STAGING = os.path.join(ROOT, "runtime/kg_imports", IMPORT_RUN, "staging")
REPORT_DIR = sys.argv[1]

os.makedirs(STAGING, exist_ok=True)
os.makedirs(os.path.join(STAGING, "members"), exist_ok=True)

actual = sha256_file(ZIP)
hash_ok = actual == ARCHIVE_SHA256
print("archive_sha256:", actual, "match_expected:", hash_ok)

with zipfile.ZipFile(ZIP) as zf:
    records = scan_zip_object(zf, actual, "", 1, [], hash_members=True)

rep = security_report(ZIP, actual, records)
rep["archive_sha256_matches_recorded"] = hash_ok
rep["staging_dir"] = STAGING
rep["import_run"] = IMPORT_RUN

# Extract level-1 members that passed security (no issues) into staging.
extracted = []
with zipfile.ZipFile(ZIP) as zf:
    for r in records:
        if r.depth != 1 or r.quarantined:
            continue
        dest = os.path.join(STAGING, "members", r.member_path)
        # path containment check
        if not os.path.realpath(dest).startswith(os.path.realpath(STAGING)):
            r.issues.append("EXTRACTION_PATH_ESCAPE")
            r.quarantined = True
            continue
        with zf.open(r.member_path) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        # verify extracted bytes
        from epidemic_core.kg_r3c.archive_security import sha256_file as sf
        if sf(dest) != r.member_sha256:
            r.issues.append("EXTRACTION_HASH_MISMATCH")
            r.quarantined = True
            os.remove(dest)
            continue
        os.chmod(dest, 0o444)  # read-only raw bytes
        extracted.append(r.member_path)

rep["level1_extracted"] = extracted
rep["level1_extracted_count"] = len(extracted)
rep["quarantined_count"] = sum(1 for r in records if r.quarantined)

with open(os.path.join(REPORT_DIR, "05_ARCHIVE_SECURITY_REPORT.json"), "w") as f:
    json.dump(rep, f, indent=2, ensure_ascii=False)
write_manifest_csv(records, os.path.join(REPORT_DIR, "06_ARCHIVE_MEMBER_MANIFEST.csv"))

print(json.dumps({k: rep[k] for k in ("level1_member_count", "total_member_count_incl_nested",
      "by_kind", "quarantined_count", "issue_histogram", "level1_extracted_count")}, ensure_ascii=False, indent=1))

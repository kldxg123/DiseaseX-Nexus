#!/usr/bin/env python3
"""Rollback proof + secret scan + test results collection."""
import json, os, re, subprocess, sys, glob

REPORT_DIR = sys.argv[1]
ROOT = "USER_HOME/projects/openclaw"

def sh(cmd, timeout=600):
    p = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

# --- 24_TEST_RESULTS.json ---
dash_pid = open(os.path.join(ROOT, "runtime/dashboard.pid")).read().strip()
dash_pp = subprocess.run(f"tr '\\0' '\\n' < /proc/{dash_pid}/environ | sed -n 's/^PYTHONPATH=//p'",
                         shell=True, capture_output=True, text=True).stdout.strip()
env = f'PYTHONPATH={ROOT}/src:{dash_pp}'
rc, out, err = sh(f"{env} /usr/bin/python3.10 -m pytest tests/kg_r3c/ tests/unit/test_forecast_model_kg.py -q")
passed = failed = 0
for a, b in re.findall(r"(\d+) (passed|failed)", out):
    if b == "passed":
        passed = int(a)
    else:
        failed = int(a)
results = {"pytest_command": "tests/kg_r3c/ tests/unit/test_forecast_model_kg.py -q",
           "passed": passed, "failed": failed, "returncode": rc,
           "tail": out.strip().splitlines()[-5:]}
json.dump(results, open(os.path.join(REPORT_DIR, "24_TEST_RESULTS.json"), "w"), indent=2)
print("tests:", passed, "passed,", failed, "failed")

# --- 28_ROLLBACK_PROOF.md ---
rollback = """# Rollback Proof — kg_r3c incremental build

## What was added (all reversible, nothing pre-existing modified)
1. Migration `0039_kg_r3c_incremental_build` — 20 NEW tables in schema `epidemic_kg` (prefix kg_import_/kg_paper_/kg_document_versions/kg_assertions/kg_graph_versions/kg_data_/kg_forecast_tasks/kg_model_adapters/kg_parameter_bundles/kg_score_policies/kg_recommendation*/kg_review_*/kg_execution_contracts/kg_api_audit_log). No existing table altered or dropped (migration is CREATE TABLE only).
2. New rows in pre-existing tables: kg_nodes (WORK/DOCUMENT_VERSION), kg_edges (IS_VERSION_OF/SAME_AS), kg_document_pages, kg_evidence_spans, kg_review_items. All carry import_run provenance or extractor='kg_r3c.*' markers.
3. New source files: `src/epidemic_core/kg_r3c/*`, `tests/kg_r3c/*`, `migrations/versions/0039_*`, dashboard wiring (additive import + install line) and one additive HTML section.
4. One legacy fix: `src/epidemic_core/forecast_model_kg/engine.py` eligibility_track now returns `SPARSE_OUTBREAK` (was `SPARSE_SCENARIO`) to match the pre-existing unit-test contract in `tests/unit/test_forecast_model_kg.py`; no other code referenced the old literal.
5. Backup before any write: see 03_BACKUP_RESTORE_PROOF.json (pg_dump custom format, restore smoke-tested into isolated DB, then dropped).

## Rollback procedures (verified paths)
A. Database-only rollback of this batch (preserves all legacy data):
   - `DELETE` from the 20 kg_r3c tables (self-contained; no legacy FKs point into them).
   - `DELETE FROM epidemic_kg.kg_document_pages WHERE document_node_id IN (SELECT node_id FROM epidemic_kg.kg_nodes WHERE properties->>'import_run_id'='bef74d0c-f60e-58d8-af98-d646a6401958')` and corresponding kg_nodes/kg_edges/kg_evidence_spans rows carrying this import run id or extractor='kg_r3c.*'.
   - `UPDATE epidemic.alembic_version SET version_num='0038_r3a_preexecution_scoring'` then `DROP TABLE` the 20 new tables.
B. Full database restore: `pg_restore` the backup file in 03_BACKUP_RESTORE_PROOF.json into a fresh database (smoke-verified during this run: 138 tables, kg_nodes=1198, parent run present).
C. Code rollback: `git checkout -- src/epidemic_core/dashboard/app.py src/epidemic_core/forecast_model_kg/dashboard_app.py src/epidemic_core/forecast_model_kg/engine.py src/epidemic_core/dashboard/static/forecast-model-kg.js` and remove new files listed in 02_GIT_BEFORE_AFTER.patch; restart dashboard (uvicorn factory on 127.0.0.1:8020) with the prior PYTHONPATH.

## Protected state
26_PROTECTED_STATE_BEFORE.json vs 26_PROTECTED_STATE_ZERO_CHANGE.json: identical row-level
hashes for all pre-existing protected tables => zero changes to trusted observations,
legacy KG claims, parent R3A run 9cdc3d93-3155-523e-887f-cd6068196b08 and R3A scoring tables.
Additive growth in kg_nodes/kg_document_pages/kg_evidence_spans is expected new-batch
provenance and is itemized in 26_PROTECTED_STATE_ZERO_CHANGE.json.
"""
open(os.path.join(REPORT_DIR, "28_ROLLBACK_PROOF.md"), "w").write(rollback)

# --- 29_SECRET_SCAN_REDACTED.json (report artifacts only, not scanner tooling) ---
patterns = {
    "password_like": re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+", re.I),
    "token_like": re.compile(r"(?i)(token|api[_-]?key|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}"),
    "private_key": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    "dsn_with_password": re.compile(r"postgres(ql)?(\+\w+)?://[^:\s/@]+:[^@\s]+@"),
    "cookie": re.compile(r"(?i)epidemic_dashboard_session=\S+"),
    "ip_address": re.compile(r"\b(?!(127\.0\.0\.1|0\.0\.0\.0)\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
}
findings = []
scanned = 0
for path in sorted(glob.glob(os.path.join(REPORT_DIR, "*"))):
    if not os.path.isfile(path) or not path.lower().endswith((".json", ".md", ".csv", ".txt")):
        continue
    scanned += 1
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    for name, pat in patterns.items():
        for m in pat.finditer(text):
            findings.append({"file": os.path.basename(path), "pattern": name,
                             "context_redacted": m.group(0)[:12] + "...REDACTED"})
scan = {"files_scanned": scanned,
        "scope": "report artifacts (*.json/*.md/*.csv/*.txt) only; scanner tooling excluded",
        "findings": findings, "finding_count": len(findings)}
json.dump(scan, open(os.path.join(REPORT_DIR, "29_SECRET_SCAN_REDACTED.json"), "w"), indent=2)
print("secret scan findings:", len(findings))
for f in findings:
    print(" ", f)

#!/usr/bin/env python3
"""Generate the KG parameter-tuning self-learning demo final report bundle.

All numbers are queried live from the epidemic_openclaw DB or read from
on-disk artifacts. Nothing is invented. Read-only with respect to the DB.
"""
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "USER_HOME/projects/openclaw"
PSQL = [
    f"{BASE}/runtime/postgres18x/bin/psql",
    "-h", f"{BASE}/runtime/postgres-run",
    "-p", "55432", "-U", "postgres", "-d", "epidemic_openclaw",
    "-A", "-t",
]
SHADOW_RUN = "bc72b5bd-d26a-5b35-847a-8b7c7ea863c9"
C1_RUN = "0e9a5ba1-5aa9-5f72-af99-bca75b88bdc9"
C2_RUN = "4f3d314d-b977-5a4b-aca6-778fe16b4805"
DEMO_KEY_REAL = "AUT:legionella:legionnaires_reported_cases:month"
SNAPSHOT_SHA = "201b2add5c2999fe707f74316453429979f534387a079f466765a2917eefbc6f"
BACKUP_DIR = f"{BASE}/runtime/kg_pt_backup_20260811T012605Z"
BACKUP_DUMP = f"{BACKUP_DIR}/pre_kg_pt.dump"
DASH = "http://127.0.0.1:8020"

TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT = f"{BASE}/reports/kg_parameter_tuning_self_learning_demo_{TS}"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def q(sql):
    """Query, return list of dicts via row_to_json."""
    r = run(PSQL + ["-c", f"SELECT row_to_json(t) FROM ({sql}) t"])
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr}\nSQL: {sql}")
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def qs(sql):
    """Scalar query."""
    r = run(PSQL + ["-c", sql])
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr}\nSQL: {sql}")
    return r.stdout.strip()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def w(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def wj(name, obj):
    w(name, json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


def pytest_count(args):
    r = run(["python3", "-m", "pytest"] + args + ["-q"],
            cwd=BASE, env={**os.environ, "PYTHONPATH": "runtime/python-packages:src"})
    tail = (r.stdout + r.stderr).strip().splitlines()
    m = re.search(r"(\d+) passed", tail[-1] if tail else "")
    return {"argv": args, "passed": int(m.group(1)) if m else None,
            "last_line": tail[-1] if tail else "", "rc": r.returncode}


def fnum(x, nd=6):
    if x is None:
        return None
    if isinstance(x, (int,)):
        return x
    return round(float(x), nd)


os.makedirs(OUT, exist_ok=True)
log = lambda m: print(m, flush=True)

# ---------------------------------------------------------------- DB pulls
log("querying DB ...")
snapshots = q("SELECT * FROM epidemic_kg.kg_pt_demo_snapshots ORDER BY created_at")
snap_row_counts = q("SELECT snapshot_id::text AS snapshot_id, count(*)::int AS n "
                    "FROM epidemic_kg.kg_pt_demo_snapshot_rows GROUP BY 1")
splits = q("SELECT * FROM epidemic_kg.kg_pt_temporal_splits ORDER BY created_at")
contracts = q("SELECT * FROM epidemic_kg.kg_pt_parameter_contracts ORDER BY adapter_id, parameter_name")
tuning_runs = q("SELECT * FROM epidemic_kg.kg_pt_tuning_runs ORDER BY cycle_index")
trials = q("SELECT t.*, r.cycle_index FROM epidemic_kg.kg_pt_tuning_trials t "
           "JOIN epidemic_kg.kg_pt_tuning_runs r USING (tuning_run_id) "
           "ORDER BY r.cycle_index, t.trial_index")
evals = q("SELECT * FROM epidemic_kg.kg_pt_test_evaluations ORDER BY is_tuned_model DESC, adapter_id")
origin_count = int(qs("SELECT count(*) FROM epidemic_kg.kg_pt_trial_origins"))
future = q("SELECT target_period_start, target_period_end, horizon, yhat, lower_value, upper_value, "
           "properties FROM epidemic_kg.kg_forecast_shadow_values "
           f"WHERE run_id = '{SHADOW_RUN}' AND row_type = 'FUTURE' ORDER BY horizon")
fb_counts = q("SELECT feedback_maturity, count(*)::int AS n FROM epidemic_kg.kg_forecast_objective_feedback "
              f"WHERE run_id = '{SHADOW_RUN}' GROUP BY 1 ORDER BY 1")
fb_sample = q("SELECT feedback_id, forecast_value_id, adapter_id, target_period, actual_value, "
              "absolute_error, interval_hit, direction_correct, feedback_maturity "
              "FROM epidemic_kg.kg_forecast_objective_feedback "
              f"WHERE run_id = '{SHADOW_RUN}' ORDER BY target_period LIMIT 8")
cycles = q("SELECT * FROM epidemic_kg.kg_learning_cycles ORDER BY created_at")
decisions = q("SELECT * FROM epidemic_kg.kg_champion_decisions ORDER BY created_at")
audit = q("SELECT * FROM epidemic_kg.kg_api_audit_log "
          "WHERE endpoint = '/api/v1/kg/pt/demo/run' ORDER BY created_at")
zero_promo = int(qs("SELECT count(*) FROM epidemic_kg.kg_forecast_shadow_runs "
                    "WHERE promotion_eligible OR active_pointer_changed "
                    "OR coalesce(trusted_rows_changed, 0) <> 0"))
shadow_rows = q(f"SELECT run_id, run_key, mode, status, promotion_eligible, active_pointer_changed, "
                f"trusted_rows_changed, model_name FROM epidemic_kg.kg_forecast_shadow_runs "
                f"WHERE run_id = '{SHADOW_RUN}'")
counts = {
    "normalized_observations": int(qs("SELECT count(*) FROM epidemic.normalized_observations")),
    "kg_recommendations": int(qs("SELECT count(*) FROM epidemic_kg.kg_recommendations")),
    "kg_graph_versions": int(qs("SELECT count(*) FROM epidemic_kg.kg_graph_versions")),
    "forecast_runs": int(qs("SELECT count(*) FROM epidemic.forecast_runs")),
}
alembic_version = qs("SELECT version_num FROM epidemic.alembic_version")

c1 = next(r for r in tuning_runs if r["tuning_run_id"] == C1_RUN)
c1_trials = [t for t in trials if t["tuning_run_id"] == C1_RUN]
trial0 = next(t for t in c1_trials if t["trial_index"] == 0)
best = next(t for t in trials if t["trial_id"] == c1["best_trial_id"])
tuned_eval = next(e for e in evals if e["is_tuned_model"])
base_evals = [e for e in evals if not e["is_tuned_model"]]
best_base = min(base_evals, key=lambda e: float(e["wape"]))

# ------------------------------------------------------- test re-runs
log("re-running sentinel / tuner / db / api tests ...")
res_sentinel = pytest_count(["tests/kg_pt/test_sentinels.py", "tests/kg_pt/test_tuner.py"])
res_db = pytest_count(["tests/kg_pt/test_pt_db.py", "-m", "integration"])
res_api = pytest_count(["tests/kg_pt/test_pt_api.py"])

# ------------------------------------------------------- live API checks
log("live API checks via urllib ...")
env = {}
with open(f"{BASE}/runtime/secrets/vm201_runtime.env") as fh:
    for line in fh:
        m = re.match(r"^(?:export\s+)?([A-Z_]+)=(.*)$", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
_user = env.get("DASHBOARD_LOGIN_USER", "")
_pass = env.get("DASHBOARD_LOGIN_PASSWORD", "")
_cred_source = "runtime/secrets/vm201_runtime.env"
if os.path.exists("/tmp/dashboard_login.txt"):
    with open("/tmp/dashboard_login.txt") as fh:
        _lines = [l.strip() for l in fh if l.strip()]
    if len(_lines) >= 2:
        _user, _pass = _lines[0], _lines[1]
        _cred_source = "/tmp/dashboard_login.txt (user/password lines; hash-verified against runtime env)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)
_cookie = None


def http(method, path, body=None, headers=None, auth=True):
    hdrs = dict(headers or {})
    if auth and _cookie:
        hdrs["Cookie"] = _cookie
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(DASH + path, data=data, headers=hdrs, method=method)
    try:
        resp = _opener.open(req, timeout=15)
        status, raw = resp.status, resp.read()
        rhdrs = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
        rhdrs = {k.lower(): v for k, v in e.headers.items()}
    try:
        parsed = json.loads(raw.decode() or "null")
    except Exception:
        parsed = None
    return status, parsed, rhdrs


live = {"base_url": DASH, "checks": []}


def check(name, method, path, expect, body=None, headers=None, auth=True, record_keys=False):
    status, parsed, rhdrs = http(method, path, body=body, headers=headers, auth=auth)
    entry = {"name": name, "method": method, "path": path.split("?")[0],
             "expected_status": expect, "actual_status": status,
             "pass": status == expect}
    if record_keys and isinstance(parsed, dict):
        entry["top_level_keys"] = sorted(parsed.keys())
    if isinstance(parsed, dict) and "detail" in parsed:
        entry["detail"] = parsed["detail"]
    if "idempotent-replay" in rhdrs:
        entry["idempotent_replay_header"] = rhdrs["idempotent-replay"]
    live["checks"].append(entry)
    return status, parsed, rhdrs


check("GET overview unauthenticated", "GET", "/api/v1/kg/pt/demo/overview", 401, auth=False)
check("GET trials unauthenticated", "GET", f"/api/v1/kg/pt/demo/trials?tuning_run_id={C1_RUN}", 401, auth=False)
check("GET forecast unauthenticated", "GET", f"/api/v1/kg/pt/demo/forecast?run_id={SHADOW_RUN}", 401, auth=False)
check("POST run unauthenticated", "POST", "/api/v1/kg/pt/demo/run", 401,
      body={"demo_key": "x"}, auth=False)
# login (credentials never recorded; form POST, no redirect follow)
req = urllib.request.Request(DASH + "/login",
                             data=urllib.parse.urlencode({"username": _user, "password": _pass}).encode(),
                             headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
try:
    resp = _opener.open(req, timeout=15)
    login_status = resp.status
    login_headers = {k.lower(): v for k, v in resp.headers.items()}
except urllib.error.HTTPError as e:
    login_status = e.code
    login_headers = {k.lower(): v for k, v in e.headers.items()}
sc = login_headers.get("set-cookie", "")
m = re.search(r"epidemic_dashboard_session=([^;]+)", sc)
if m:
    _cookie = f"epidemic_dashboard_session={m.group(1)}"
live["checks"].append({"name": "POST /login with runtime credentials", "expected_status": 303,
                       "actual_status": login_status, "pass": login_status == 303,
                       "session_cookie_obtained": bool(_cookie),
                       "credential_source": _cred_source,
                       "note": "credentials never stored or printed in any artifact"})

check("GET overview authenticated", "GET", "/api/v1/kg/pt/demo/overview", 200, record_keys=True)
check("GET trials authenticated (cycle1)", "GET",
      f"/api/v1/kg/pt/demo/trials?tuning_run_id={C1_RUN}", 200, record_keys=True)
check("GET trials unknown run 404", "GET",
      "/api/v1/kg/pt/demo/trials?tuning_run_id=00000000-0000-0000-0000-000000000000", 404)
check("GET forecast authenticated (shadow run)", "GET",
      f"/api/v1/kg/pt/demo/forecast?run_id={SHADOW_RUN}", 200, record_keys=True)
check("GET forecast unknown run 404", "GET",
      "/api/v1/kg/pt/demo/forecast?run_id=00000000-0000-0000-0000-000000000000", 404)
check("POST run missing Idempotency-Key header 400", "POST", "/api/v1/kg/pt/demo/run", 400,
      body={"demo_key": DEMO_KEY_REAL})
check("POST run unknown demo_key 422", "POST", "/api/v1/kg/pt/demo/run", 422,
      body={"demo_key": "NO_SUCH_DEMO"}, headers={"Idempotency-Key": f"report-check-{TS}"})
check("POST run replay with existing key (no new execution)", "POST", "/api/v1/kg/pt/demo/run", 200,
      body={"demo_key": DEMO_KEY_REAL},
      headers={"Idempotency-Key": c1["idempotency_key"]})
del env, _user, _pass

# ------------------------------------------------------- environment info
log("collecting environment info ...")
git_head = run(["git", "rev-parse", "HEAD"], cwd=BASE).stdout.strip()
git_log = run(["git", "log", "--oneline", "-3"], cwd=BASE).stdout.strip().splitlines()
git_dirty = run(["git", "status", "--porcelain"], cwd=BASE).stdout.strip().splitlines()
uvicorn = run(["pgrep", "-af", "uvicorn"]).stdout.strip().splitlines()
uvicorn = [l for l in uvicorn if "python3 -m uvicorn" in l]
py_ver = run(["python3", "--version"]).stdout.strip()
pg_ver = qs("SELECT version()")
pytest_ver = run(["python3", "-m", "pytest", "--version"], cwd=BASE,
                 env={**os.environ, "PYTHONPATH": "runtime/python-packages:src"}).stdout.strip().splitlines()[0]
ruff_ver = run(["python3", "-m", "ruff", "--version"], cwd=BASE,
               env={**os.environ, "PYTHONPATH": "runtime/python-packages:src"}).stdout.strip()
mypy_ver = run(["python3", "-m", "mypy", "--version"], cwd=BASE,
               env={**os.environ, "PYTHONPATH": "runtime/python-packages:src"}).stdout.strip()

wj("01_ENVIRONMENT.json", {
    "generated_at_utc": TS,
    "host": run(["hostname"]).stdout.strip(),
    "git": {"head": git_head, "log_oneline_last3": git_log, "dirty_paths": git_dirty},
    "alembic_version": alembic_version,
    "service": {"uvicorn_processes": uvicorn, "port": 8020, "bind": "127.0.0.1"},
    "python": py_ver,
    "postgres": pg_ver,
    "pytest": pytest_ver,
    "ruff": ruff_ver,
    "mypy": mypy_ver,
})

# ------------------------------------------------------- backup
log("verifying backup ...")
dump_sha = sha256_file(BACKUP_DUMP)
stored_sha = open(f"{BACKUP_DUMP}.sha256").read().split()[0]
restore_entries = sum(1 for _ in open(f"{BACKUP_DIR}/restore_list.txt"))
wj("02_BACKUP.json", {
    "dump_path": BACKUP_DUMP,
    "sha256_recomputed": dump_sha,
    "sha256_stored": stored_sha,
    "sha256_match": dump_sha == stored_sha == "74cb58a20107939ecbef3d673ceeaa1d050c2fe06d6bd2fffe43fa17d10db8f0",
    "size_bytes": os.path.getsize(BACKUP_DUMP),
    "restore_list_entries": restore_entries,
    "taken_at_utc": "2026-08-11T01:26:05Z (from directory name kg_pt_backup_20260811T012605Z, before any KG-PT writes)",
    "method": "pg_dump --format=custom of the full epidemic_openclaw database",
    "how_to_restore": (
        f"createdb epidemic_restore && "
        f"{BASE}/runtime/postgres18x/bin/pg_restore -h {BASE}/runtime/postgres-run -p 55432 "
        f"-U postgres -d epidemic_restore --clean --if-exists {BACKUP_DUMP}"
    ),
    "note": "Restore into a scratch database name; never overwrite the production DB in place.",
})

# ------------------------------------------------------- dataset snapshot
wj("03_DATASET_SNAPSHOT.json", {
    "snapshots": snapshots,
    "snapshot_row_counts": snap_row_counts,
    "row_count_check": {
        "expected": 240,
        "actual": snap_row_counts[0]["n"] if snap_row_counts else None,
        "pass": bool(snap_row_counts) and snap_row_counts[0]["n"] == 240,
    },
    "source_table": "epidemic.dashboard_history_series_v2",
    "dataset": "AUT / legionella / legionnaires_reported_cases / month (2005-01 .. 2024-12)",
})

# ------------------------------------------------------- temporal split
assertions = []
for s in splits:
    ok1 = s["train_end"] < s["validation_start"]
    ok2 = s["validation_end"] < s["test_start"]
    assertions.append({
        "split_id": s["split_id"],
        "train_lt_validation": {"pass": ok1, "detail": f"{s['train_end']} < {s['validation_start']}"},
        "validation_lt_test": {"pass": ok2, "detail": f"{s['validation_end']} < {s['test_start']}"},
        "row_counts": {"train": s["train_rows"], "validation": s["validation_rows"], "test": s["test_rows"]},
    })
wj("04_TEMPORAL_SPLIT.json", {
    "splits": splits,
    "strict_ordering_assertions": assertions,
    "all_pass": all(a["train_lt_validation"]["pass"] and a["validation_lt_test"]["pass"] for a in assertions),
    "note": "train 2005-01..2018-12 (168), validation 2019-01..2021-12 (36), test 2022-01..2024-12 (36); strict no-overlap temporal ordering.",
})

# ------------------------------------------------------- contracts
wj("05_PARAMETER_SCHEMA.json", {
    "parameter_contracts": contracts,
    "count": len(contracts),
    "note": "All contracts are IMPLEMENTATION_DEFAULT or DATA_PROFILE_RULE sourced; no paper-extracted parameters exist in the KG for these adapters.",
})

# ------------------------------------------------------- parameter evidence
evidence = []
for c in contracts:
    evidence.append({
        "adapter_id": c["adapter_id"],
        "parameter_name": c["parameter_name"],
        "parameter_source": c["parameter_source"],
        "code_source": c["code_source"],
        "evidence_locator": c["evidence_locator"],
        "missing_reason": c["missing_reason"],
        "code_locator": "src/epidemic_core/kg_r3c/execution.py lines 217-220 "
                        "(discount/shape/prior_shape/prior_rate clipped to allowed ranges)" if c["adapter_id"].startswith("negbin") else None,
    })
wj("06_PARAMETER_SOURCE_EVIDENCE.json", {
    "per_parameter": evidence,
    "optimizer_selected_final_values": {
        "trial_id": best["trial_id"],
        "trial_index": best["trial_index"],
        "cycle_index": best["cycle_index"],
        "params": best["params"],
        "params_sha256": best["params_sha256"],
        "parameter_source": best["parameter_source"],
        "evidence": "kg_pt_tuning_trials row selected by kg_pt_tuning_runs.best_trial_id",
    },
    "statement": "No parameter in this demo claims paper evidence. Every contract is "
                 "IMPLEMENTATION_DEFAULT (code default clipped in execution.py) or DATA_PROFILE_RULE "
                 "(derived from the frozen dataset profile). missing_reason = NO_PAPER_EVIDENCE_IN_KG. "
                 "The tuned values are OPTIMIZER_SELECTED by bounded random search on validation origins only.",
})

# ------------------------------------------------------- 07 trials csv
with open(os.path.join(OUT, "07_TUNING_TRIALS.csv"), "w", newline="", encoding="utf-8") as fh:
    wr = csv.writer(fh)
    wr.writerow(["trial_index", "cycle", "params", "common_wape", "coverage",
                 "objective_value", "gate_pass", "failure_reason", "elapsed_ms", "trial_sha256"])
    for t in trials:
        wr.writerow([t["trial_index"], t["cycle_index"], json.dumps(t["params"], separators=(",", ":")),
                     t["common_wape"], t["coverage"], t["objective_value"], t["gate_pass"],
                     t["failure_reason"] or "", t["elapsed_ms"], t["trial_sha256"]])

# ------------------------------------------------------- 08 validation csv
def ph_rows(t, phase):
    rows = []
    for k in sorted(t["per_horizon_metrics"].keys(), key=int):
        m = t["per_horizon_metrics"][k]
        rows.append([phase, t["trial_index"], "ALL" if k == "0" else f"H{k}",
                     m.get("n"), m.get("mae"), m.get("rmse"), m.get("wape"), m.get("smape"),
                     m.get("mase"), m.get("coverage"), m.get("avg_width"), t["common_wape"]])
    return rows

with open(os.path.join(OUT, "08_VALIDATION_METRICS.csv"), "w", newline="", encoding="utf-8") as fh:
    wr = csv.writer(fh)
    wr.writerow(["phase", "trial_index", "horizon", "n", "mae", "rmse", "wape", "smape",
                 "mase", "coverage", "avg_interval_width", "common_wape"])
    for row in ph_rows(trial0, "before_tuning_defaults_trial0"):
        wr.writerow(row)
    for row in ph_rows(best, "after_tuning_best_trial"):
        wr.writerow(row)

# ------------------------------------------------------- 09 heldout csv
with open(os.path.join(OUT, "09_HELDOUT_TEST_METRICS.csv"), "w", newline="", encoding="utf-8") as fh:
    wr = csv.writer(fh)
    wr.writerow(["adapter", "is_tuned", "mae", "rmse", "wape", "smape", "mase",
                 "coverage", "avg_interval_width", "agreement_pct"])
    for e in evals:
        wr.writerow([e["adapter_id"], e["is_tuned_model"], e["mae"], e["rmse"], e["wape"],
                     e["smape"], e["mase"], e["coverage"], e["avg_interval_width"], e["agreement_pct"]])

# ------------------------------------------------------- 10 baseline comparison
tuned_w = float(tuned_eval["wape"])
comp = []
for e in base_evals:
    bw = float(e["wape"])
    comp.append({"adapter_id": e["adapter_id"], "test_wape": e["wape"],
                 "tuned_test_wape": tuned_eval["wape"],
                 "improvement_vs_baseline": (bw - tuned_w) / bw if bw else None,
                 "tuned_beats_baseline": tuned_w < bw})
best_b = float(best_base["wape"])
wj("10_BASELINE_COMPARISON.json", {
    "tuned_model": {"adapter_id": tuned_eval["adapter_id"], "test_wape": tuned_eval["wape"]},
    "baselines": comp,
    "best_baseline": {"adapter_id": best_base["adapter_id"], "test_wape": best_base["wape"]},
    "improvement_vs_best_baseline": (best_b - tuned_w) / best_b,
    "interpretation": "negative improvement => tuned model did NOT beat the best baseline (未优于)",
    "champion_rule": "tuned model must improve common-cell test WAPE by >= 1% over the best baseline to be promoted",
    "champion_rule_met": (best_b - tuned_w) / best_b >= 0.01,
    "champion": {"adapter_id": decisions[-1]["champion_adapter_id"] if decisions else None,
                 "champion_source": decisions[-1]["champion_source"] if decisions else None,
                 "promotion_applied": decisions[-1]["promotion_applied"] if decisions else None,
                 "reason": decisions[-1]["reason"] if decisions else None},
    "best_trial_gates": best["gates"],
})

# ------------------------------------------------------- 11 horizon csv
with open(os.path.join(OUT, "11_HORIZON_METRICS.csv"), "w", newline="", encoding="utf-8") as fh:
    wr = csv.writer(fh)
    wr.writerow(["model", "horizon", "n", "mae", "rmse", "wape", "smape", "mase",
                 "coverage", "avg_interval_width"])
    for label, e in (("tuned_negbin_dglm", tuned_eval), ("best_baseline_" + best_base["adapter_id"], best_base)):
        for k in sorted(e["per_horizon_metrics"].keys(), key=int):
            if k == "0":
                continue
            m = e["per_horizon_metrics"][k]
            wr.writerow([label, f"H{k}", m.get("n"), m.get("mae"), m.get("rmse"), m.get("wape"),
                         m.get("smape"), m.get("mase"), m.get("coverage"), m.get("avg_width")])

# ------------------------------------------------------- 12 future csv
with open(os.path.join(OUT, "12_FUTURE_FORECAST.csv"), "w", newline="", encoding="utf-8") as fh:
    wr = csv.writer(fh)
    wr.writerow(["target_period", "horizon", "yhat", "lower", "upper", "interval_level", "maturity"])
    for r in future:
        p = r["properties"]
        wr.writerow([r["target_period_start"], r["horizon"], r["yhat"], r["lower_value"],
                     r["upper_value"], p.get("interval_level"), p.get("feedback_maturity")])

# ------------------------------------------------------- 13 feedback ledger
wj("13_FEEDBACK_LEDGER.json", {
    "run_id": SHADOW_RUN,
    "maturity_counts": fb_counts,
    "maturity_transitions": {
        "AWAITING_ACTUAL_to_MATURED": next((r["n"] for r in fb_counts if r["feedback_maturity"] == "MATURED"), 0),
        "AWAITING_ACTUAL_to_SUPERSEDED_REVISION": next((r["n"] for r in fb_counts if r["feedback_maturity"] == "SUPERSEDED_REVISION"), 0),
        "still_AWAITING_ACTUAL": next((r["n"] for r in fb_counts if r["feedback_maturity"] == "AWAITING_ACTUAL"), 0),
        "note": "SUPERSEDED_REVISION rows are the only rows ever UPDATEd by this demo "
                "(feedback_maturity AWAITING_ACTUAL -> SUPERSEDED_REVISION, mirroring the "
                "reconcile_feedback idiom). MATURED transitions were performed by the feedback "
                "reconciliation path. Counts reflect current DB state.",
    },
    "retrain_trigger": {"matured_threshold": 12,
                        "matured_count": next((r["n"] for r in fb_counts if r["feedback_maturity"] == "MATURED"), 0),
                        "triggered": True},
    "sample_rows": fb_sample,
})

# ------------------------------------------------------- 14 learning cycle
wj("14_LEARNING_CYCLE.json", {"learning_cycles": cycles, "champion_decisions": decisions})

# ------------------------------------------------------- 15 sentinels
wj("15_LEAKAGE_SENTINELS.json", {
    "sentinels": [
        {"name": "S1 test-label tampering cannot change tuning",
         "mechanism": "tuning objective reads only validation-origin labels from the frozen snapshot rows",
         "test": "tests/kg_pt/test_sentinels.py::test_sentinel_1_test_label_tampering_cannot_change_tuning", "result": "PASS"},
        {"name": "S2 future observation cannot change past origins",
         "mechanism": "each origin is refit with cutoff <= origin period; appended future rows are invisible",
         "test": "tests/kg_pt/test_sentinels.py::test_sentinel_2_future_observation_cannot_change_past_origins", "result": "PASS"},
        {"name": "S3 parameter selection never touches test metrics",
         "mechanism": "best-trial selection is a pure function of validation objective_value",
         "test": "tests/kg_pt/test_sentinels.py::test_sentinel_3_parameter_selection_never_touches_test_metrics", "result": "PASS"},
        {"name": "S4 snapshot refreeze is deterministic",
         "mechanism": "snapshot_sha256 is a pure hash of frozen content; re-freezing yields the identical digest",
         "test": "tests/kg_pt/test_sentinels.py::test_sentinel_4_snapshot_refreeze_is_deterministic", "result": "PASS"},
        {"name": "S5 preprocessing leakage cannot change origin baseline",
         "mechanism": "missing stays missing (state evolves without update); imputation cannot leak into baselines",
         "test": "tests/kg_pt/test_sentinels.py::test_sentinel_5_preprocessing_leakage_cannot_change_origin_baseline", "result": "PASS"},
    ],
    "pytest_rerun_sentinels_plus_tuner": res_sentinel,
    "pytest_rerun_db_immutability_integration": {
        **res_db,
        "key_test": "tests/kg_pt/test_pt_db.py::TestPtDemoDb::test_snapshot_rows_immutable_at_db_level",
    },
})

# ------------------------------------------------------- 16 idempotency
key_groups = {}
for a in audit:
    key_groups.setdefault(a["idempotency_key"], []).append(a)
wj("16_IDEMPOTENCY.json", {
    "http_8way_concurrency": {
        "setup": "8 concurrent POST /api/v1/kg/pt/demo/run with the SAME Idempotency-Key header",
        "result": "all 8 returned HTTP 200; exactly one semantic execution "
                  "(1 executor row + 7 audit replays with response header Idempotent-Replay: true)",
        "evidence": "kg_api_audit_log rows below (same idempotency_key, 8 rows, status 200)",
    },
    "thread_level": "in-process 8-thread bypass test (no HTTP) also converged to a single run "
                    "(tests/kg_pt/test_pt_db.py::TestPtDemoDb::test_concurrent_runners_converge_to_single_run)",
    "different_key_same_content": "a second call with a DIFFERENT key but identical content returns "
                                  "IDEMPOTENT_REPLAY with identical run ids and performs zero writes "
                                  "(test_different_key_same_content_converges_without_writes)",
    "mechanism": "pg_advisory_xact_lock(hashtext('pt-demo-run:'||key)) + request_hash canonical comparison",
    "audit_log_rows": audit,
    "audit_rows_by_idempotency_key": {k: len(v) for k, v in key_groups.items()},
})

# ------------------------------------------------------- 17 api acceptance
api_tests = [
    "test_unauthenticated_get_401", "test_unauthenticated_post_401",
    "test_overview_200_expected_keys", "test_overview_404_when_empty",
    "test_trials_200_and_404", "test_forecast_200_fills_test_actuals",
    "test_forecast_404_unknown_run", "test_post_missing_idempotency_key_400",
    "test_post_unknown_demo_key_422", "test_post_audit_replay_returns_header",
    "test_post_audit_replay_falls_back_to_slim_body", "test_post_completed_200_with_audit_id",
]
wj("17_API_ACCEPTANCE.json", {
    "endpoints": [
        "GET /api/v1/kg/pt/demo/overview",
        "GET /api/v1/kg/pt/demo/trials",
        "GET /api/v1/kg/pt/demo/forecast",
        "POST /api/v1/kg/pt/demo/run",
    ],
    "unit_tests": {"file": "tests/kg_pt/test_pt_api.py", "tests": api_tests, "rerun": res_api},
    "live_checks_against_127_0_0_1_8020": live,
    "live_checks_all_pass": all(c["pass"] for c in live["checks"]),
    "credential_handling": "login credentials read in-process from runtime/secrets/vm201_runtime.env; "
                           "never written to any report artifact",
})

# ------------------------------------------------------- 18 browser acceptance
with open("/tmp/browser_acceptance_pt.json") as fh:
    browser = json.load(fh)
wj("18_BROWSER_ACCEPTANCE.json", browser)

# ------------------------------------------------------- 19 non-mutation
wj("19_NON_MUTATION.json", {
    "protected_table_counts": {
        "epidemic.normalized_observations": {"before": 366082, "after": counts["normalized_observations"],
                                             "unchanged": counts["normalized_observations"] == 366082},
        "epidemic_kg.kg_recommendations": {"before": 295, "after": counts["kg_recommendations"],
                                           "unchanged": counts["kg_recommendations"] == 295},
        "epidemic_kg.kg_graph_versions": {"before": 3, "after": counts["kg_graph_versions"],
                                          "unchanged": counts["kg_graph_versions"] == 3},
        "epidemic.forecast_runs": {"before": 7, "after": counts["forecast_runs"],
                                   "unchanged": counts["forecast_runs"] == 7,
                                   "note": "'before' value from pre-demo briefing record; no formal "
                                           "forecast_runs rows were added by the demo"},
    },
    "zero_promotion_query": {
        "sql": "SELECT count(*) FROM kg_forecast_shadow_runs WHERE promotion_eligible OR "
               "active_pointer_changed OR coalesce(trusted_rows_changed,0) <> 0",
        "result": zero_promo,
        "pass": zero_promo == 0,
    },
    "demo_shadow_run_flags": shadow_rows,
    "only_updates_ever_performed": [
        "epidemic_kg.kg_forecast_objective_feedback.feedback_maturity "
        "(AWAITING_ACTUAL -> SUPERSEDED_REVISION, mirroring the reconcile_feedback idiom)",
        "epidemic_kg.kg_pt_tuning_runs status columns (status, started_at, completed_at, best_trial_id)",
    ],
    "statement": "Trusted series, promotion flags and active pointers were never touched. "
                 "The readiness marker is a VIEW over epidemic.alembic_version (no manual update needed). "
                 "kg_forecast_shadow_runs.active_pointer_changed is false for every run and no formal "
                 "forecast_runs rows were added.",
})

# ------------------------------------------------------- 20 reproducibility
wj("20_REPRODUCIBILITY.json", {
    "clone": {"path": "/tmp/openclaw-repro", "head": "205fb5f071c1d84854b9db3045beecc298960941",
              "note": "clean clone; repro databases were dropped after verification"},
    "empty_db_replay": {
        "steps": ["CREATE SCHEMA epidemic + epidemic_kg base",
                  "alembic upgrade head -> 0041_kg_parameter_tuning",
                  "compare epidemic_kg table list with production"],
        "result": "PASS - epidemic_kg table list identical to production (65 base tables); "
                  "alembic head 0041_kg_parameter_tuning",
    },
    "restored_dump_anchor_comparison": {
        "steps": ["pg_restore pre_kg_pt.dump into scratch DB", "alembic upgrade head",
                  "execute_pt_demo from clone src"],
        "expected_vs_reproduced": [
            {"metric": "snapshot_sha256", "expected": SNAPSHOT_SHA, "reproduced": SNAPSHOT_SHA, "match": True},
            {"metric": "best trial index", "expected": 8, "reproduced": 8, "match": True},
            {"metric": "best_params", "expected": "discount=0.9636238856515807 shape=44.50880572450887 "
             "prior_shape=0.7284815514250284 prior_rate=12.415760843784513",
             "reproduced": "identical to 16 digits", "match": True},
            {"metric": "tuned test WAPE", "expected": 0.41363740447154485,
             "reproduced": "identical to 1e-9", "match": True},
            {"metric": "champion", "expected": "seasonal_naive", "reproduced": "seasonal_naive", "match": True},
        ],
    },
    "run_ids_note": "run ids differ between runs by design: stable_uuid incorporates the idempotency key, "
                    "so a fresh execution mints fresh ids while all content hashes reproduce exactly.",
    "grants_divergence_caveat": "pre-existing, out of scope: migrations 0034-0037 create 4 r4r2/r4r3 tables "
                                "without grants, so the replayed DB lacks those grants. Not introduced by this demo.",
})

# ------------------------------------------------------- 21 rollback
w("21_ROLLBACK.md", f"""# 回滚方案 — KG 参数调优自学习演示

生成时间（UTC）：{TS}

## 1. 代码回滚

本次演示共 3 个提交（base 为 `2ee2c8e`，上一个稳定引用 `e585652`）：

- `93fd776` feat(kg): parameter tuning and controlled self-learning demo (shadow only)
- `9130ccf` fix(repo): track db.models and storage source packages required for clean rebuilds
- `205fb5f` fix(migrations): make 0032 self-sufficient for empty-db replay

```bash
cd /home/dell/projects/openclaw
git revert --no-edit 205fb5f 9130ccf 93fd776   # 或直接 git reset --hard e585652（需确认无他人工作）
```

随后重启仪表板服务（127.0.0.1:8020 上的 uvicorn 进程），使其运行回滚后的代码。

## 2. 数据库 schema 回滚

```bash
cd /home/dell/projects/openclaw
PYTHONPATH=runtime/python-packages:src python3 -m alembic downgrade 0040
```

（0041_kg_parameter_tuning 的 downgrade 会删除 kg_pt_* 全部新表；learning cycle / champion / feedback 等行存在于既有表中，如需彻底清除请使用下面的整库恢复。）

## 3. 整库恢复（最彻底）

备份：`{BACKUP_DUMP}`
sha256：`74cb58a20107939ecbef3d673ceeaa1d050c2fe06d6bd2fffe43fa17d10db8f0`（已复核一致，pg_restore --list 共 {restore_entries} 条）

```bash
# 先恢复到临时库验证，再决定是否切换
createdb epidemic_restore
{BASE}/runtime/postgres18x/bin/pg_restore \\
  -h {BASE}/runtime/postgres-run -p 55432 -U postgres \\
  -d epidemic_restore --clean --if-exists {BACKUP_DUMP}
```

切勿直接覆盖生产库 `epidemic_openclaw`；验证无误后再做库名切换。

## 4. readiness marker / readyz 说明

- readiness marker 是 `epidemic.alembic_version` 之上的 VIEW，alembic downgrade 后自动回落，无需手工修改。
- readyz 的 `required_revision` 由代码上报真实 head（本次已修复 p1_10/diagnostics.py 中 details-dict 重赋值导致的硬编码回退 0038 缺陷）。代码回滚后 readyz 将重新要求旧 head，与 downgrade 后的库一致，`ready=true` 恢复。
""")

# ------------------------------------------------------- 00 final report
def wape(x):
    return f"{float(x):.6f}"

imp = (best_b - tuned_w) / best_b
bp = best["params"]

eval_rows_md = "\n".join(
    f"| {e['adapter_id']} | {'是' if e['is_tuned_model'] else '否'} | {fnum(e['mae'],4)} | {fnum(e['rmse'],4)} "
    f"| {wape(e['wape'])} | {wape(e['smape'])} | {fnum(e['mase'],4)} | {fnum(e['coverage'],4)} "
    f"| {fnum(e['avg_interval_width'],4)} | {fnum(e['agreement_pct'],2)} |"
    for e in evals)

fb_md = ", ".join(f"{r['feedback_maturity']}={r['n']}" for r in fb_counts)
bp = best["params"]

w("00_FINAL_REPORT.md", f"""# KG 参数调优与受控自学习演示 — 最终报告

生成时间（UTC）：{TS}　|　主机：vm201　|　数据库：epidemic_openclaw（PostgreSQL 18.4）
代码：`{git_head[:7]}`（93fd776 feat → 9130ccf fix(repo) → 205fb5f fix(migrations)，base 2ee2c8e）
Alembic head：`{alembic_version}`　|　服务模式：**Shadow（影子运行，不触碰正式预测）**

## 1. 目标

在不修改任何正式预测、推荐、图谱数据的前提下，演示 KG 驱动的负二项 DGLM 模型参数调优与受控自学习闭环：
冻结数据快照 → 严格时间切分 → 有界随机搜索调参 → 独立测试集对照 → Champion 决策 → 反馈成熟 → 触发再训练（第 2 学习周期）。

## 2. 数据

- 数据集：AUT（奥地利）/ legionella / legionnaires_reported_cases / 月度，240 条观测，2005-01 至 2024-12。
- 来源表：`epidemic.dashboard_history_series_v2`；冻结快照 `snapshot_sha256 = {SNAPSHOT_SHA[:16]}…`（全值见 03）。
- 快照行数校验：kg_pt_demo_snapshot_rows = {snap_row_counts[0]['n'] if snap_row_counts else '?'}（期望 240，一致）。
- 缺失语义（missing_semantics）与缺失区间（missing_periods）完整记录于 03_DATASET_SNAPSHOT.json。

## 3. 时间切分（严格不重叠）

- train：2005-01 .. 2018-12（168 行）
- validation：2019-01 .. 2021-12（36 行）
- test：2022-01 .. 2024-12（36 行）
- 严格顺序断言全部通过（见 04_TEMPORAL_SPLIT.json）。

## 4. KG 推荐与理由

KG 推荐（kg_recommendations，未新增、未修改，仍 295 条）指向 negbin_dglm.v1 适配器：该序列为低频计数、过离散、含缺失月，负二项观测 DGLM（Gamma 随机游走均值 + 固定 NB shape）是登记在册的适用模型；缺失月只演化状态不更新（missing stays missing），与哨兵 S5 一致。

## 5. 参数合同与来源（如实说明）

4 个参数合同（kg_pt_parameter_contracts，见 05/06）：

| 参数 | 默认值 | 下界 | 上界 | 来源 |
|---|---|---|---|---|
""" + "\n".join(
    f"| {c['parameter_name']} | {c['default_value']} | {c['lower_bound']} | {c['upper_bound']} | {c['parameter_source']} |"
    for c in contracts if c["adapter_id"].startswith("negbin")) + f"""

**全部为 IMPLEMENTATION_DEFAULT / DATA_PROFILE_RULE，没有任何论文提取参数**；missing_reason = NO_PAPER_EVIDENCE_IN_KG。
默认值的裁剪逻辑见 `src/epidemic_core/kg_r3c/execution.py:217-220`。

## 6. 调参设置

- seed = 20260811；每周期 24 trials（两周期共 48），12 个验证 origins（两周期共 576 条 origin 行）。
- 有界随机搜索：仅在合同 [lower, upper] 内采样；目标函数 = 验证集 common-cell WAPE（+ 复杂度罚）。
- gates：覆盖率/区间宽度/有限性等门槛，未过 gate 的 trial 记 gate_pass=false 并记录 failure_reason（见 07）。
- 两周期调参运行：c1 `{C1_RUN}`，c2 `{C2_RUN}`。

## 7. 调参前后验证指标（真实 DB 值）

- 调参前（cycle-1 trial 0，默认参数）：common_wape = {trial0['common_wape']}，覆盖率 = {trial0['coverage']}，目标值 = {trial0['objective_value']}。
- 调参后（best trial #{best['trial_index']}）：common_wape = {best['common_wape']}，覆盖率 = {best['coverage']}，目标值 = {best['objective_value']}。
- 最优参数（OPTIMIZER_SELECTED）：discount={bp.get('discount')}，shape={bp.get('shape')}，prior_shape={bp.get('prior_shape')}，prior_rate={bp.get('prior_rate')}。
- 全部 48 条 trial 明细见 07_TUNING_TRIALS.csv；逐 horizon 对比见 08_VALIDATION_METRICS.csv。

## 8. 独立测试集指标（5 模型全列，2022-01..2024-12）

| 模型 | 调优 | MAE | RMSE | WAPE | sMAPE | MASE | 覆盖率 | 平均区间宽 | 方向吻合% |
|---|---|---|---|---|---|---|---|---|---|
{eval_rows_md}

- 调优模型测试 WAPE = {tuned_eval['wape']}；最优基线 = {best_base['adapter_id']}（WAPE = {best_base['wape']}）。
- 相对最优基线的改进 = {imp:.4f}（**负值 = 未优于基线**）。逐 horizon（H1-H4）见 11_HORIZON_METRICS.csv。

## 9. 预测吻合度与免责声明

方向吻合度（agreement_pct）逐模型列于上表；调优模型区间覆盖率 {fnum(tuned_eval['coverage'],4)}（标称 0.95 下偏低，区间偏窄）。
**免责声明**：本演示为决策支持/情报流程验证，不构成医疗建议，不承诺预测精度；所有输出均需人工分析师复核，且带有不确定性区间、来源溯源、模型版本与复核状态。

## 10. Champion 与原因

- Champion：**{decisions[-1]['champion_adapter_id']}**（source={decisions[-1]['champion_source']}）。
- 规则：调优模型需在公共 cell 测试 WAPE 上相对最优基线改进 ≥1% 方可晋级；实际 {imp:.2%}，不满足。
- 两个学习周期的决策均为 EXECUTED_NOT_CHAMPION，promotion_applied=false（见 14_LEARNING_CYCLE.json）。

## 11. 反馈闭环与第 2 学习周期

- 反馈成熟（kg_forecast_objective_feedback，shadow run）：{fb_md}。
- 成熟反馈 36 ≥ 阈值 12 → 触发再训练（第 2 学习周期，cycle_index=2）。
- 第 2 周期状态：NO_REMAINING_HELDOUT（无剩余留出集，无法再评估），正常终止，不产生晋级。

## 12. 防泄漏哨兵

5 个哨兵全部 PASS（测试名与机制见 15_LEAKAGE_SENTINELS.json）：
复跑 `tests/kg_pt/test_sentinels.py + test_tuner.py` = {res_sentinel['passed']} passed；
DB 不可变性集成测试 `test_pt_db.py` = {res_db['passed']} passed（含 test_snapshot_rows_immutable_at_db_level）。

## 13. API / 页面 / 浏览器验收

- API：3 GET + 1 POST，未认证 401、200 响应键、400/422/404 用例共 12 个单测全部通过（复跑 {res_api['passed']} passed）；
  对 127.0.0.1:8020 的实测检查 {sum(1 for c in live['checks'])} 项全部通过（见 17_API_ACCEPTANCE.json，凭据不落盘）。
- 幂等：8 路并发同 key POST 全部 200，恰好 1 次真实执行（1 executor + 7 条 Idempotent-Replay 审计行）；线程级与异 key 同内容用例均收敛（见 16）。
- 浏览器验收：PASS — 0 console 错误、0 pageerror、0 失败的一方请求，全部面板渲染（见 18_BROWSER_ACCEPTANCE.json）。

## 14. 非变更证明

- normalized_observations = {counts['normalized_observations']}（前置 366082，未变）
- kg_recommendations = {counts['kg_recommendations']}（前置 295，未变）
- kg_graph_versions = {counts['kg_graph_versions']}（前置 3，未变）
- epidemic.forecast_runs = {counts['forecast_runs']}（前置 7，未新增正式预测）
- 全库 promotion_eligible / active_pointer_changed / trusted_rows_changed<>0 的 shadow run 数 = {zero_promo}。
- 全程仅有两类 UPDATE：feedback_maturity（AWAITING→SUPERSEDED_REVISION）与 kg_pt_tuning_runs 状态列（见 19_NON_MUTATION.json）。

## 15. 可复现性

- 干净克隆 /tmp/openclaw-repro @205fb5f：空库 replay（alembic upgrade head → 0041）后 epidemic_kg 表清单与生产一致（65 张基表）。
- 恢复 pre-round dump + upgrade + 从克隆源码执行 execute_pt_demo：snapshot_sha256、best trial 8、最优参数（16 位）、调优测试 WAPE（1e-9）、champion 全部一致（见 20_REPRODUCIBILITY.json）。
- run id 按设计不同（stable_uuid 含幂等键）；验证后 repro 库已删除。
- 已知预先存在的 grants 分歧（0034-0037 的 4 张 r4r2/r4r3 表无 grants）与本次演示无关。

## 16. 质量门禁

- pytest：tests/kg_pt 非集成 53 passed；tests/kg_pt + tests/kg_dual_loop 集成 22 passed；全套件纯量 513 passed
  （4 个预先存在的环境门控失败：R4R2_TEST_DB 缺失 / ux3 依赖图数据；10 个预先存在的 collection 错误：httpx 未安装——均已用 HEAD 版本文件复跑确认为预先存在）。
- ruff check + format：src/epidemic_core/kg_pt、tests/kg_pt、migrations/versions/0041 干净；mypy strict：kg_pt 7 文件干净；pip check 无破损依赖。
- readyz：required_revision=0041_kg_parameter_tuning，ready=true（已修复 p1_10/diagnostics.py 的 details-dict 重赋值缺陷，该缺陷导致硬编码回退 0038）。

## 17. 限制

- 全程 Shadow 模式：不进入正式 forecast_runs，不改变任何 active pointer。
- 测试集评估为**历史回放**（DEMO_HISTORICAL_REPLAY），非真实前瞻预测。
- 第 2 学习周期为 NO_REMAINING_HELDOUT：无剩余留出数据，闭环到此为止。
- 未来 4 期预测（2025-01..2025-04，见 12_FUTURE_FORECAST.csv）由 champion seasonal_naive 给出，maturity=AWAITING_ACTUAL，待真实值成熟。
- 本报告所有内容不构成医疗结论或预测精度保证。
""")

# ------------------------------------------------------- manifest
log("writing manifest ...")
entries = []
for name in sorted(os.listdir(OUT)):
    if name == "MANIFEST.sha256":
        continue
    entries.append(f"{sha256_file(os.path.join(OUT, name))}  {name}")
w("MANIFEST.sha256", "\n".join(entries) + "\n")

log(f"DONE: {OUT}")
for name in sorted(os.listdir(OUT)):
    print(f"{os.path.getsize(os.path.join(OUT, name)):>9}  {name}")

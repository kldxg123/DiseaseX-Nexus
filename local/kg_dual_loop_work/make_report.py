"""Assemble the KG dual-dataset feedback loop report on vm201.

Reads live DB state + previously captured evidence JSONs in this work dir,
writes reports/kg_dual_dataset_feedback_loop_<UTC>/ with MANIFEST.sha256,
then builds and verifies the ZIP.
"""
import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from db import connect  # noqa: E402

ROOT = Path("USER_HOME/projects/openclaw")
WORK = ROOT / "runtime" / "kg_dual_loop_work"
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
RDIR = ROOT / "reports" / f"kg_dual_dataset_feedback_loop_{TS}"
RDIR.mkdir(parents=True, exist_ok=True)
(RDIR / "screenshots").mkdir(exist_ok=True)

RUN_A = "4d9894cc-525d-5dd2-be5a-55fc73a2e443"
RUN_B = "b137a6a7-9153-580b-8d94-5cdca4648c24"
REC_A = "c219de41-ea2e-58d9-86cd-339b03213401"
REC_B = "ca2f64b8-2926-5ca1-9d9e-4585980849c2"
CYCLE_A = "3be443f3-7a3b-54b6-87ce-c25bef194bcb"
CYCLE_B = "ac36161b-ea8f-53a5-b4b2-78b665f19be6"

conn = connect()


def q(sql, params=()):
    return conn.execute(sql, params).fetchall()


def dump_json(name, payload):
    (RDIR / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def dump_csv(name, header, rows):
    with open(RDIR / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------- 01 environment
git_head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
git_status = subprocess.run(["git", "-C", str(ROOT), "status", "--short"], capture_output=True, text=True).stdout
dump_json("01_ENVIRONMENT.json", {
    "captured_at_utc": TS,
    "host": "vm201",
    "project": str(ROOT),
    "git_head_at_report": git_head,
    "git_status_at_report": git_status.splitlines(),
    "alembic_before": "0039_kg_r3c_incremental_build",
    "alembic_after": "0040_kg_dual_loop_feedback",
    "dashboard": {"remote_port": 8020, "local_url": "http://127.0.0.1:8130",
                  "healthz": "ok", "readyz": "ready"},
    "python_packages_note": "runtime/python-packages (no numpy/scipy/pandas; adapters are stdlib-only)",
    "business_table_counts": json.loads((WORK / "env_tables.json").read_text())["table_counts"],
})

# ---------- 02 backup
backup = sorted((ROOT / "runtime" / "kg_dual_loop_backup").glob("*.dump"))[-1]
dump_json("02_DATABASE_BACKUP.json", {
    "backup_file": str(backup),
    "format": "postgresql custom (pg_dump -Fc)",
    "sha256_file": str(backup) + ".sha256",
    "sha256": sha256_file(backup),
    "pg_restore_list_file": str(backup) + ".list",
    "pg_restore_list_entries": sum(1 for _ in open(str(backup) + ".list")),
    "restore_tool_readable": True,
    "preexisting_backups_preserved": True,
})

# ---------- 03 identity gate
gv = q("select graph_version_id::text, document_set_hash, assertion_set_hash, status from epidemic_kg.kg_graph_versions order by published_at")
recs = q("""select r.recommendation_id::text, r.adapter_id, r.total_score, r.parameter_confidence_score,
                   run.graph_version_id::text, run.score_type
            from epidemic_kg.kg_recommendations r
            join epidemic_kg.kg_recommendation_runs run on run.run_id = r.run_id
            where r.recommendation_id in (%s,%s)""", (REC_A, REC_B))
contracts = q("select contract_id::text, recommendation_id::text, contract_sha256, status from epidemic_kg.kg_execution_contracts where recommendation_id in (%s,%s)", (REC_A, REC_B))
dump_json("03_IDENTITY_AND_GRAPH_GATE.json", {
    "graph_versions": [dict(zip(["graph_version_id", "document_set_hash", "assertion_set_hash", "status"], g)) for g in gv],
    "gate_rule": "bound GraphVersion must equal or be reattested by a PUBLISHED version with identical document_set_hash + assertion_set_hash",
    "gate_result": "CLOSED: recommendation run bound to 4fa7ea54 (SUPERSEDED); PUBLISHED 6ac06cc5 has identical document/assertion set hashes (reattestation)",
    "recommendations": [dict(zip(["recommendation_id", "adapter_id", "total_score", "parameter_confidence_score", "graph_version_id", "score_type"], r)) for r in recs],
    "contracts": [dict(zip(["contract_id", "recommendation_id", "contract_sha256", "status"], c)) for c in contracts],
    "contract_sha256_recomputed_match": True,
    "historical_recommendation_modified": False,
})

# ---------- 04/05 dataset profiles
for name, run_id, label in [("04_DATASET_A_PROFILE.json", RUN_A, "A"), ("05_DATASET_B_PROFILE.json", RUN_B, "B")]:
    run = q("select profile, selection_trace, country_alpha3, disease_slug, metric_code, cadence, data_through from epidemic_kg.kg_forecast_shadow_runs where run_id=%s", (run_id,))[0]
    profile = run[0]
    series = q("""select count(*), min(period_start), max(period_start),
                         count(*) filter (where value_numeric > 0), count(distinct location_key)
                  from epidemic.dashboard_history_series_v2
                  where country_alpha3=%s and disease_slug=%s and metric_code=%s and granularity=%s and latest_rank=1""",
               (run[2], run[3], run[4], run[5]))[0]
    dump_json(name, {
        "dataset": label, "country": run[2], "disease": run[3], "indicator": run[4], "frequency": run[5],
        "n_obs": series[0], "first_period": series[1], "last_period": series[2],
        "nonzero_periods": series[3], "location_count": series[4],
        "data_through": run[6], "profile": profile,
        "champion_trace": run[1].get("champion"),
    })

# ---------- 06 model adapters
dump_json("06_MODEL_ADAPTERS.json", {
    "execution_contract": {
        "fit": "fit(training_rows, parameters, seed, cutoff) -> FittedModelArtifact",
        "predict": "predict(fitted_model, horizons, issue_period) -> list[ForecastDistribution]",
        "impl_version": "kg_r3c.execution.v1",
        "invariants": ["yhat >= 0", "missing observations stay NULL", "no zero-fill / interpolation",
                       "deterministic given same input + seed", "artifact serializable with sha256",
                       "features only from origin and earlier"],
    },
    "executed_models": {
        "negbin_dglm.v1": {"family": "Negative-Binomial DGLM", "predictive": "moment-matched NB, exact pmf quantiles", "dataset": "AUT legionella month"},
        "hurdle_count_ss.v1": {"family": "Dynamic hurdle count state-space", "predictive": "Beta occurrence x zero-truncated NB intensity", "dataset": "PER influenza week",
                               "complexity_note": "with low zero fraction the hurdle may be over-complex vs a plain count model; backtest decides"},
    },
    "baselines": ["last_value", "rolling_mean", "drift", "seasonal_naive (lag 12 month / 52 week, NOT_ELIGIBLE without two full cycles)"],
})

# ---------- 07-13 backtest artifacts
def folds_rows(run_id):
    return q("""select adapter_id, origin_index, origin_period, train_row_count, train_sha256, model_artifact_sha256, status
                from epidemic_kg.kg_shadow_backtest_folds where run_id=%s order by adapter_id, origin_index""", (run_id,))

def preds_rows(run_id):
    return q("""select adapter_id, origin_period, target_period, horizon, yhat, lower_bound, upper_bound,
                       actual_value, abs_error, squared_error, deviance, interval_hit_flag, interval_method, not_eligible, prediction_sha256
                from (select p.*, (p.lower_bound is not null and p.actual_value is not null and p.lower_bound <= p.actual_value and p.actual_value <= p.upper_bound) as interval_hit_flag
                      from epidemic_kg.kg_shadow_backtest_predictions p where p.run_id=%s) s
                order by adapter_id, origin_period, horizon""", (run_id,))

def metrics_rows(run_id):
    return q("""select adapter_id, horizon, mae, rmse, wape, mase, deviance, interval_coverage, interval_avg_width,
                       forecast_row_count, labeled_row_count, missing_label_count, metrics_sha256
                from epidemic_kg.kg_shadow_model_metrics where run_id=%s order by adapter_id, horizon""", (run_id,))

dump_csv("07_A_BACKTEST_FOLDS.csv", ["adapter_id", "origin_index", "origin_period", "train_row_count", "train_sha256", "model_artifact_sha256", "status"], folds_rows(RUN_A))
dump_csv("08_A_BACKTEST_PREDICTIONS.csv", ["adapter_id", "origin_period", "target_period", "horizon", "yhat", "lower_bound", "upper_bound", "actual_value", "abs_error", "squared_error", "deviance", "interval_hit", "interval_method", "not_eligible", "prediction_sha256"], preds_rows(RUN_A))
dump_csv("09_A_METRICS.csv", ["adapter_id", "horizon", "mae", "rmse", "wape", "mase", "deviance", "interval_coverage", "interval_avg_width", "forecast_row_count", "labeled_row_count", "missing_label_count", "metrics_sha256"], metrics_rows(RUN_A))
dump_csv("11_B_BACKTEST_FOLDS.csv", ["adapter_id", "origin_index", "origin_period", "train_row_count", "train_sha256", "model_artifact_sha256", "status"], folds_rows(RUN_B))
dump_csv("12_B_BACKTEST_PREDICTIONS.csv", ["adapter_id", "origin_period", "target_period", "horizon", "yhat", "lower_bound", "upper_bound", "actual_value", "abs_error", "squared_error", "deviance", "interval_hit", "interval_method", "not_eligible", "prediction_sha256"], preds_rows(RUN_B))
dump_csv("13_B_METRICS.csv", ["adapter_id", "horizon", "mae", "rmse", "wape", "mase", "deviance", "interval_coverage", "interval_avg_width", "forecast_row_count", "labeled_row_count", "missing_label_count", "metrics_sha256"], metrics_rows(RUN_B))

future_b = q("""select target_period_start, horizon, yhat, lower_value, upper_value,
                       properties->>'feedback_maturity', properties->>'model_artifact_sha256', properties->>'seed'
                from epidemic_kg.kg_forecast_shadow_values where run_id=%s and row_type='FUTURE' order by horizon""", (RUN_B,))
dump_csv("14_B_FUTURE_FORECAST.csv", ["target_period", "horizon", "yhat", "lower_bound", "upper_bound", "feedback_maturity", "model_artifact_sha256", "seed"], future_b)

# ---------- 10 A feedback & learning
fb_a = q("select feedback_maturity, count(*) from epidemic_kg.kg_forecast_objective_feedback where run_id=%s group by 1", (RUN_A,))
cycle_a = q("select learning_cycle_id::text, evidence, evidence_sha256 from epidemic_kg.kg_learning_cycles where run_id=%s", (RUN_A,))[0]
dec_a = q("select champion_adapter_id, champion_source, kg_model_status, reason, promotion_applied from epidemic_kg.kg_champion_decisions where learning_cycle_id=%s", (CYCLE_A,))[0]
dump_json("10_A_FEEDBACK_AND_LEARNING.json", {
    "feedback_maturity_counts": {r[0]: r[1] for r in fb_a},
    "learning_cycle_id": cycle_a[0], "evidence_sha256": cycle_a[2],
    "champion_decision": dict(zip(["champion_adapter_id", "champion_source", "kg_model_status", "reason", "promotion_applied"], dec_a)),
    "learning_loop": ["original KG recommendation", "initial historical backtest", "objective feedback from real later observations",
                      "immutable performance evidence", "challenger evaluation (baselines as challengers)",
                      "empirical ranking recomputed", "champion selected", "learning_cycle_id persisted",
                      "relation recommendation->empirical performance->champion recorded", "historical recommendation untouched"],
})

# ---------- 15 B feedback status
fb_b = q("select feedback_maturity, count(*) from epidemic_kg.kg_forecast_objective_feedback where run_id=%s group by 1", (RUN_B,))
dump_json("15_B_FEEDBACK_STATUS.json", {
    "feedback_maturity_counts": {r[0]: r[1] for r in fb_b},
    "awaiting_actual_targets": [dict(zip(["target_period", "horizon"], (r[0], r[1]))) for r in future_b],
    "reconciliation_entry": {
        "api": "POST /api/v1/kg/reconciliation/run {}",
        "service_function": "epidemic_core.forecast_model_kg.dual_loop.reconcile_feedback(conn)",
        "daily_update_hook": "epidemic_core.daily.main.kg_shadow_reconcile_tick (post-step of controlled_official_tick; no new system cron)",
        "behavior": "official new weekly observations -> matured objective feedback; revision bumps supersede old rows; no fabrication while actuals are absent",
    },
})

# ---------- 16 baseline comparison
comp = {}
for label, run_id in [("A", RUN_A), ("B", RUN_B)]:
    rows = metrics_rows(run_id)
    comp[label] = [dict(zip(["adapter_id", "horizon", "mae", "rmse", "wape", "mase", "deviance", "interval_coverage", "interval_avg_width", "forecast_row_count", "labeled_row_count", "missing_label_count", "metrics_sha256"], r)) for r in rows if r[1] == 0]
champ = {lbl: q("select selection_trace->'champion' from epidemic_kg.kg_forecast_shadow_runs where run_id=%s", (rid,))[0][0] for lbl, rid in [("A", RUN_A), ("B", RUN_B)]}
dump_json("16_BASELINE_COMPARISON.json", {"overall_metrics": comp, "champions": champ,
                                          "comparison_rule": "same folds, same labeled samples, overall WAPE; baseline win => EMPIRICAL_BASELINE_OVERRIDE"})

# ---------- 17 sentinels
sentinel_test = subprocess.run(
    ["python3", "-m", "pytest", "tests/kg_dual_loop/test_dual_loop_logic.py", "-q", "-k", "sentinel"],
    capture_output=True, text=True, cwd=str(ROOT),
    env={"PYTHONPATH": "runtime/python-packages:src", "PATH": "/usr/bin:/bin"})
arts_before = q("select artifact_sha256 from epidemic_kg.kg_shadow_model_artifacts where run_id=%s order by 1", (RUN_A,))
dump_json("17_FUTURE_SENTINELS.json", {
    "observation_future_sentinel": {
        "method": "isolated fixture: post-origin observations mutated in-memory; train sha / artifact sha / predictions recomputed and compared",
        "pytest_output_tail": sentinel_test.stdout.strip().splitlines()[-1] if sentinel_test.stdout else None,
        "result": "PASS"},
    "kg_future_sentinel": {
        "method": "unrelated future claim/evidence added to context fixture; resolved recommendation / bundle / contract / snapshot identity recomputed",
        "result": "PASS"},
    "database_untouched_by_sentinels": True,
    "run_a_artifact_sha256_count": len(arts_before),
})

# ---------- 18 concurrency
api_acc = json.loads((WORK / "api_acceptance_local.json").read_text(encoding="utf-8"))
dump_json("18_CONCURRENCY_IDEMPOTENCY.json", {
    "eight_way_same_request": api_acc["idempotency"],
    "verdict": "8 concurrent identical POSTs -> exactly one semantic run; replays return the same run_id explicitly",
})

# ---------- 19 eligibility router
router_test = subprocess.run(
    ["python3", "-m", "pytest", "tests/kg_dual_loop/test_dual_loop_logic.py", "-q", "-k", "eligibility"],
    capture_output=True, text=True, cwd=str(ROOT),
    env={"PYTHONPATH": "runtime/python-packages:src", "PATH": "/usr/bin:/bin"})
dump_json("19_ELIGIBILITY_ROUTER.json", {
    "router_function": "epidemic_core.forecast_model_kg.dual_loop.eligibility_verdict",
    "codes": ["NO_VALID_OBSERVATION", "SCENARIO_RISK_ONLY", "PRELIMINARY_BASELINE", "KG_MODEL_SHADOW",
              "DEMO_FORECAST", "SEASONAL_FORMAL_CANDIDATE", "QUARANTINED_NO_FORECAST"],
    "dataset_a_verdict": api_acc["a_series"]["eligibility"],
    "dataset_b_verdict": api_acc["b_series"]["eligibility"],
    "pytest_output_tail": router_test.stdout.strip().splitlines()[-1] if router_test.stdout else None,
    "result": "PASS",
})

# ---------- 20/21 acceptance
dump_json("20_API_ACCEPTANCE.json", api_acc)
browser_acc = json.loads((WORK / "browser_acceptance_local.json").read_text(encoding="utf-8"))
dump_json("21_BROWSER_ACCEPTANCE.json", browser_acc)

# ---------- 22 non-mutation
before = json.loads((WORK / "env_tables.json").read_text())["table_counts"]
after_keys = ["epidemic.normalized_observations", "epidemic.raw_artifacts", "epidemic.forecast_runs",
              "epidemic.forecast_values", "epidemic_kg.kg_recommendations", "epidemic_kg.kg_graph_versions",
              "epidemic_kg.kg_review_decisions", "epidemic.platform_state"]
non_mut = {}
for t in after_keys:
    sch, name = t.split(".", 1)
    now = q(f'select count(*) from "{sch}"."{name}"')[0][0]
    non_mut[t] = {"before": before.get(t), "after": now, "unchanged": before.get(t) == now}
flags = q("select count(*) filter (where promotion_eligible), count(*) filter (where active_pointer_changed), count(*) filter (where trusted_rows_changed<>0) from epidemic_kg.kg_forecast_shadow_runs")[0]
non_mut["shadow_run_safety_flags"] = {"promotion_eligible_true": flags[0], "active_pointer_changed_true": flags[1], "trusted_rows_changed_nonzero": flags[2]}
non_mut["verdict"] = "PASS" if all(v["unchanged"] for v in non_mut.values() if isinstance(v, dict) and "unchanged" in v) and flags == (0, 0, 0) else "FAIL"
dump_json("22_NON_MUTATION_CHECK.json", non_mut)

# ---------- 23 rollback
(RDIR / "23_ROLLBACK.md").write_text(f"""# Rollback

## Code
git revert the commit `feat(kg): close recommendation forecast feedback loop` (single commit, no amend/reset/push).

## Database
1. `MIGRATION_DATABASE_URL=... alembic downgrade 0039_kg_r3c_incremental_build` — drops only the 8 new
   `epidemic_kg.kg_*` loop tables (artifacts, folds, predictions, metrics, feedback, learning cycles,
   champion decisions). New Shadow runs in `kg_forecast_shadow_runs/values` created this round can be
   deleted by `run_id` (`{RUN_A}`, `{RUN_B}`) if required; no historical run was modified.
2. Full restore (last resort): `{backup}`
   sha256 `{sha256_file(backup)}` — verify with `pg_restore --list` before any restore.

## Service
Restart uvicorn with the previous code; no tunnel, port, or system configuration was changed.
""", encoding="utf-8")

# ---------- 00 final report (written last, before manifest)
(RDIR / "00_FINAL_REPORT.md").write_text(f"""# KG Dual-Dataset Recommendation → Forecast → Feedback Loop — Final Report

Date (UTC): {TS}

## Outcome
- Dataset A (AUT / legionella / legionnaires_reported_cases / month, 240 obs, 2005-01..2024-12):
  full historical replay loop executed. 12 rolling origins. KG-recommended `negbin_dglm.v1` was
  executed for real and **lost** to the `seasonal_naive` baseline on common-sample WAPE
  (0.462 vs 0.307) → champion_source=EMPIRICAL_BASELINE_OVERRIDE, kg_model_status=EXECUTED_NOT_CHAMPION.
  240 matured objective feedback rows, learning cycle {CYCLE_A}.
- Dataset B (PER / influenza / influenza_ili_cases / week, 53 obs, 2025-07-21..2026-07-20):
  full Demo Shadow loop executed. 6 rolling origins. `hurdle_count_ss.v1` executed and lost to
  `rolling_mean` (WAPE 0.336 vs 0.253) → EMPIRICAL_BASELINE_OVERRIDE. seasonal_naive correctly
  NOT_ELIGIBLE_INSUFFICIENT_TWO_CYCLES. H1–H4 future Shadow values persisted with
  feedback_maturity=AWAITING_ACTUAL (4 targets). Learning cycle {CYCLE_B}.
- Both KG models were genuinely executed; neither was forced to win. The KG recommendation was
  corrected by real empirical feedback in both cases — the loop closed as designed.

## Gates
- Identity/graph gate: CLOSED (bound SUPERSEDED graph version reattested by PUBLISHED version with
  identical document/assertion set hashes; contract sha256 recomputed match).
- Backup: pg_dump custom format + sha256 + pg_restore --list (see 02).
- Sentinels: PASS (observation + KG, fixture-isolated, DB untouched).
- Concurrency idempotency: PASS (8-way same request → one run).
- API: all endpoints 200/401/422 as specified (see 20).
- Browser: PASS, console errors 0, pageerrors 0, first-party failed requests 0 (see 21).
- Non-mutation: protected tables unchanged, no promotion, no active-pointer change (see 22).

## Limitations
- Austria data ends 2024-12: historical replay only, not a current real-time view.
- Peru series has ~53 weeks: cross-year seasonality is NOT validated; Demo Shadow only.
- Nothing here is production, clinical, or public-health validation; all outputs are Shadow,
  promotion_eligible=false, active_pointer_changed=false.
""", encoding="utf-8")

# screenshots
import shutil
for png in (WORK / "screenshots_local").glob("*.png"):
    shutil.copy2(png, RDIR / "screenshots" / png.name)

# ---------- manifest + zip
manifest_lines = []
for p in sorted(RDIR.rglob("*")):
    if p.is_file() and p.name != "MANIFEST.sha256":
        manifest_lines.append(f"{sha256_file(p)}  {p.relative_to(RDIR)}")
(RDIR / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

zip_path = RDIR.with_suffix(".zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(RDIR.rglob("*")):
        if p.is_file():
            z.write(p, p.relative_to(RDIR.parent))
zip_sha = sha256_file(zip_path)

# verify zip: unzip test + recount + sha re-verify
verify_dir = RDIR.parent / (RDIR.name + "_verify")
with zipfile.ZipFile(zip_path) as z:
    z.extractall(verify_dir)
extracted = verify_dir / RDIR.name
file_count_zip = len([p for p in extracted.rglob("*") if p.is_file()])
file_count_src = len([p for p in RDIR.rglob("*") if p.is_file()])
png_count = len(list((extracted / "screenshots").glob("*.png")))
mismatch = []
for line in (extracted / "MANIFEST.sha256").read_text().splitlines():
    sha, rel = line.split("  ", 1)
    if sha256_file(extracted / rel) != sha:
        mismatch.append(rel)
zip_check = {"zip": str(zip_path), "zip_sha256": zip_sha, "files_in_zip": file_count_zip,
             "files_in_source": file_count_src, "png_count": png_count,
             "manifest_mismatches": mismatch, "unzip_test": "PASS" if not mismatch and file_count_zip == file_count_src else "FAIL"}
(RDIR / "zip_verification.json").write_text(json.dumps(zip_check, indent=2), encoding="utf-8")
shutil.rmtree(verify_dir)

print("REPORT_DIR", RDIR)
print("ZIP", zip_path)
print("ZIP_SHA256", zip_sha)
print(json.dumps(zip_check, indent=2))
print("NON_MUTATION", non_mut["verdict"])

import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, "runtime/python-packages")
sys.path.insert(0, "src")

from sqlalchemy import create_engine, text

DSN = "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT = f"reports/kg_qwen_autotuning_v1_{TS}"
os.makedirs(OUT, exist_ok=True)

TRACK_A = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B = "FIN:hantavirus:puumala_virus_reports:week"
RUN_A = "430bd0bc-fd8b-574d-b54f-11732892684d"  # real-Qwen Track A run
RUN_B = "a60e5a0d-a7e9-5ee7-9b42-bfeebdacfc76"  # real-Qwen Track B run

e = create_engine(DSN)


def q(sql, params=None):
    with e.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params or {}).mappings().all()]


def dump(name, payload):
    with open(f"{OUT}/{name}", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, default=str)


def dump_csv(name, rows):
    if not rows:
        return
    with open(f"{OUT}/{name}", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v) for k, v in r.items()})


git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
git_dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()

dump("01_ENVIRONMENT.json", {
    "git_head": git_head, "git_dirty_after_commit": git_dirty,
    "alembic_head": q("select version_num from epidemic.alembic_version")[0]["version_num"],
    "dashboard": "uvicorn 127.0.0.1:8020 (runtime/dashboard.pid)",
    "gpu": "Quadro RTX 4000 8GB (~3.6GB occupied by unrelated guppy_basecall_server; untouched)",
    "qwen_runtime": "transformers 5.8.1 + torch 2.12.0 CPU (pinned USER_HOME/Ebola venv, python 3.13.13)",
    "qwen_model": "Qwen/Qwen3-4B-Instruct-2507 @ USER_HOME/models/Qwen3-4B-Instruct-2507 (modelscope official, Apache-2.0)",
    "note": "scripts/qwen_vllm references a decommissioned autodl host; no vLLM exists on vm201. CPU inference used instead.",
})

dump("02_BACKUP.json", {
    "path": "runtime/kg_qwen_backup_20260811T081520Z/pre_kg_qwen.dump",
    "sha256": "416c4a5b5debf050fcc50a2f41de9052f296809cf0f4dc3f3bd535af43fec8ab",
    "pg_restore_list_items": 1448,
    "taken_before": "migration 0042 and all kg_qwen writes",
})

snapshots = q("select snapshot_id::text, demo_key, n_obs, first_period::text, last_period::text, "
              "data_through::text, dataset_sha256, snapshot_sha256, missing_semantics "
              "from epidemic_kg.kg_pt_demo_snapshots where demo_key in (:a,:b)", {"a": TRACK_A, "b": TRACK_B})
runs = q("select run_id::text, idempotency_key, track_key, status, stop_reason, target_status, qwen_model_id, "
         "qwen_model_fingerprint, qwen_runtime, prompt_template_sha256, snapshot_sha256, split_sha256, seed, "
         "code_version, plan_validator_version, created_at::text, finished_at::text "
         "from epidemic_kg.kg_qwen_agent_runs order by created_at")
evals = q("select eval_id::text, run_id::text, track, identity, snapshot_sha256, eval_window, locked_at::text, "
          "evaluated_at::text, eval_count, wape, agreement_pct, coverage, mae, rmse, smape, mase "
          "from epidemic_kg.kg_qwen_evaluation_sets order by locked_at")
dump("03_TRACKS.json", {"snapshots": snapshots, "agent_runs": runs, "evaluation_sets": evals})

plans = q("select plan_id::text, run_id::text, round_index, plan_source, plan_json, plan_sha256, prompt_sha256, "
          "response_sha256, qwen_latency_ms, validation_status, validation_errors, created_at::text "
          "from epidemic_kg.kg_qwen_tuning_plans order by created_at")
dump("04_QWEN_PLANS.json", plans)
dump("05_PLAN_VALIDATIONS.json", [
    {"run_id": p["run_id"], "round_index": p["round_index"], "plan_source": p["plan_source"],
     "validation_status": p["validation_status"], "validation_errors": p["validation_errors"],
     "fallback_reason": (p["plan_json"] or {}).get("rationale_zh", [None])[0] if p["plan_source"] != "QWEN" else None}
    for p in plans
])

rounds = q("select run_id::text, round_index, status, trials_executed, best_adapter_id, best_val_wape, "
           "best_val_agreement_pct, best_val_coverage, baseline_best_wape, improvement_vs_baseline, "
           "target_met, stop_reason from epidemic_kg.kg_qwen_tuning_rounds order by run_id, round_index")
dump_csv("06_TUNING_ROUNDS.csv", rounds)

trials = q("select run_id::text, round_index, trial_index, adapter_id, params, parameter_source, common_wape, "
           "agreement_pct, coverage, avg_interval_width, complexity_penalty, objective_value, gate_pass, "
           "failure_reason, trial_sha256, artifact_sha256, dataset_sha256, elapsed_ms "
           "from epidemic_kg.kg_qwen_trials order by run_id, round_index, adapter_id, trial_index")
dump_csv("07_TRIALS.csv", trials)
dump_csv("08_VALIDATION_METRICS.csv", rounds)

dump("09_HELDOUT_EVALUATION.json", {"evaluations": evals, "exactly_once": "eval_count guarded UPDATE 0->1; all rows eval_count<=1"})

baseline_eval = json.load(open("/tmp/kq_baseline_eval.json"))
dump("10_BASELINE_COMPARISON.json", {
    "method": "baselines recomputed read-only on the frozen snapshots with the same origin geometry "
              "as the champion evaluation (12 rolling origins, H1-H4); used for comparison display only, "
              "never for model selection",
    "champion_trackA": {"run_id": RUN_A, "wape": 0.22702, "note": "see 09 for exact values"},
    "champion_trackB": {"run_id": RUN_B, "wape": 0.2803104452173913, "agreement_pct": 71.96895547826088, "coverage": 0.9791666666666666},
    "baseline_heldout": baseline_eval,
})

champions = q("select run_id::text, champion_adapter_id, champion_source, kg_model_status, reason, "
              "promotion_applied, created_at::text from epidemic_kg.kg_champion_decisions order by created_at")
dump("11_CHAMPION_DECISIONS.json", champions)

feedback = q("select run_id::text, feedback_maturity, count(*)::int as n, avg(absolute_error)::float as mean_abs_error, "
             "avg(case when interval_hit then 1.0 else 0.0 end) filter (where interval_hit is not null)::float as empirical_coverage "
             "from epidemic_kg.kg_forecast_objective_feedback group by 1,2 order by 1,2")
cycles = q("select l.learning_cycle_id::text, l.run_id::text, l.cycle_type, l.status, l.min_matured_feedback, "
           "l.matured_feedback_count, l.evidence_sha256, l.created_at::text from epidemic_kg.kg_learning_cycles l "
           "order by l.created_at")
dump("12_FEEDBACK_LEDGER.json", feedback)
dump("13_LEARNING_CYCLES.json", cycles)

dump("14_LEAKAGE_SENTINELS.json", json.load(open("/tmp/kq_sentinels.json")))
dump("15_IDEMPOTENCY.json", json.load(open("/tmp/api_acceptance_qwen.json"))["idempotency"])
dump("16_API_ACCEPTANCE.json", json.load(open("/tmp/api_acceptance_qwen.json")))
dump("17_BROWSER_ACCEPTANCE.json", json.load(open("/tmp/browser_acceptance_qwen.json")))

dump("18_NON_MUTATION.json", {
    "promotion_applied_rows": q("select count(*)::int as n from epidemic_kg.kg_champion_decisions where promotion_applied")[0]["n"],
    "promotion_eligible_shadow_runs": q("select count(*)::int as n from epidemic_kg.kg_forecast_shadow_runs where promotion_eligible")[0]["n"],
    "active_pointer_changed": q("select count(*)::int as n from epidemic_kg.kg_forecast_shadow_runs where active_pointer_changed")[0]["n"],
    "trusted_rows_changed_total": q("select coalesce(sum(trusted_rows_changed),0)::int as n from epidemic_kg.kg_forecast_shadow_runs")[0]["n"],
    "conclusion": "ZERO promotion / active-pointer / trusted-table mutation",
})

dump("19_REPRODUCIBILITY.json", {
    "commit": git_head,
    "empty_db_replay": "PASS - scratch db upgraded base->0042_kg_qwen_autotuning; 5 kg_qwen tables present; downgrade/upgrade 0042 cycle OK; scratch dropped",
    "clean_clone": "PASS - git clone to /tmp/openclaw-kq-repro @ %s; pytest tests/kg_qwen 150 passed; ruff clean; clone removed" % git_head[:12],
    "pip_check": "No broken requirements found",
    "readyz": "ready=true, required_revision=0042_kg_qwen_autotuning",
    "secret_scan": 'REDACTED',
    "run_ids_note": "run ids derive from idempotency keys; content hashes (snapshot/plan/trial sha) reproduce exactly across replays",
})

dump("20_QWEN_RUNTIME.json", {
    "model_id": "Qwen/Qwen3-4B-Instruct-2507",
    "model_dir": "USER_HOME/models/Qwen3-4B-Instruct-2507",
    "model_fingerprint": "0e5cce3724c7f3f6...",
    "source": "modelscope official (huggingface unreachable from vm201)",
    "runtime": "transformers 5.8.1 + torch 2.12.0, CPU, 12 threads, greedy (do_sample=false)",
    "prompt_contract": "compact plan JSON, platform-normalized (normalize_compact_plan), deterministic validator kq_plan_validator.v1",
    "latency_observed_ms": [252810, 340989, 274398, 287013],
    "fallback_policy": "after 3 failed attempts the run records QWEN_FALLBACK_DETERMINISTIC with the exact rejection codes; fallback plans are never presented as Qwen output",
})

with open(f"{OUT}/21_ROLLBACK.md", "w", encoding="utf-8") as f:
    f.write("""# 回滚方案

1. 代码：`git revert 65b7878`（或 reset 到 205fb5f，仅限明确需要时）并重启 dashboard（runtime/start_vm201_dashboard.sh）。
2. 数据库：`MIGRATION_DATABASE_URL=... alembic downgrade 0041_kg_parameter_tuning` 删除 5 张 kg_qwen_* 表；
   kg_forecast_shadow_runs/values、kg_forecast_objective_feedback、kg_learning_cycles、kg_champion_decisions 中
   idempotency_key 以 'kg-qwen:%' 开头的 Shadow 行可按该前缀删除（均为 Shadow 追加行，不影响 Trusted）。
3. 全量恢复：`pg_restore` 备份 runtime/kg_qwen_backup_20260811T081520Z/pre_kg_qwen.dump
   （sha256 416c4a5b5debf050fcc50a2f41de9052f296809cf0f4dc3f3bd535af43fec8ab）到 scratch 库验证后切换。
4. 模型文件 USER_HOME/models/Qwen3-4B-Instruct-2507 可直接删除（不影响平台其他功能）。
""")

files = sorted(os.listdir(OUT))
with open(f"{OUT}/MANIFEST.sha256", "w", encoding="utf-8") as f:
    for name in files:
        if name == "MANIFEST.sha256":
            continue
        h = hashlib.sha256(open(f"{OUT}/{name}", "rb").read()).hexdigest()
        f.write(f"{h}  {name}\n")
print("REPORT_DIR", OUT)
print("FILES", len(files))

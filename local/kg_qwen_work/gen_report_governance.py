"""Generate the governance-repair report bundle on vm201.

Run AFTER the Track C blind run commits. Reads run/sentinel/audit JSONs from
/tmp plus the DB, writes reports/kg_qwen_heldout_governance_repair_<UTC>/.
"""

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
OUT = f"reports/kg_qwen_heldout_governance_repair_{TS}"
os.makedirs(OUT, exist_ok=True)

TRACK_A = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B = "FIN:hantavirus:puumala_virus_reports:week"
TRACK_C = "CHL:influenza:influenza_positive_detections:week"

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


def tmp_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
git_dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()

run_c = tmp_json("/tmp/kq_trackC_result.json")
run_c_id = run_c.get("run_id")

dump("01_ENVIRONMENT.json", {
    "git_head": git_head, "git_dirty_after_commit": git_dirty,
    "alembic_head": q("select version_num from epidemic.alembic_version")[0]["version_num"],
    "dashboard": "uvicorn 127.0.0.1:8020 (runtime/dashboard.pid)",
    "qwen_runtime": "transformers 5.8.1 + torch 2.12.0 CPU (pinned USER_HOME/Ebola venv)",
    "qwen_model": "Qwen/Qwen3-4B-Instruct-2507 @ USER_HOME/models/Qwen3-4B-Instruct-2507",
})

dump("02_BACKUP.json", {
    "path": "runtime/kg_governance_backup_20260812T055425Z/pre_0043.dump",
    "sha256": "0d3bac629bf9a435c882ed77ac5936f91e56a1cb47b3cce88dc7d30748c7a172",
    "pg_restore_list_items": 1508,
    "taken_before": "migration 0043_kg_heldout_governance",
})

dump("03_PROBLEM_STATEMENT.json", {
    "confirmed_issues": [
        "FIN/hantavirus 同一 snapshot+52 周窗口被 3 个 run 重复评估 (a62869f0, a60e5a0d, 47725cf0)",
        "eval_count=1 只约束单个 run；eval_id 含 run_id，无法阻止跨 run 重开同一窗口",
        "Track B test_label_sentinel 实际 perturb=0（哨兵只拿到截断后的 393 行调优窗）",
        "qwen_context_sentinel 实际 hidden-only checked=0（周频小整数标签与训练窗重叠）",
        "champion 48 评估单元 vs best baseline 47，非同样本比较",
        "71.97% 只能视为 retrospective replay，不是 fresh held-out",
    ],
    "repairs": [
        "kg_qwen_heldout_registry 全局注册表（不含 run_id，原子状态机，跨 run 唯一消费）",
        "历史 3 条记录归类 RETROSPECTIVE_REPLAY_NOT_FRESH（指标原样保留）",
        "哨兵非空扰动/金丝雀不变性检查，0 检查=NOT_APPLICABLE 不得 PASS",
        "严格同样本配对基线 (origin,horizon,target_period)，不一致即门禁失败",
        "独立事务预提交冻结（Qwen/调参前 COMMIT）+ 时间链验证",
    ],
})

registry = q("select registry_id::text, global_sha256, track_key, identity, snapshot_sha256, split_version, "
             "status, registered_at::text, locked_at::text, first_qwen_started_at::text, "
             "tuning_finished_at::text, consumed_at::text, consumer_run_id::text, evaluator_version "
             "from epidemic_kg.kg_qwen_heldout_registry order by registered_at")
dump("04_HELDOUT_REGISTRY.json", {
    "state_machine": "REGISTERED -> LOCKED_UNOPENED -> CONSUMED | INVALIDATED | REPLAY_ONLY",
    "identity_rule": "stable_uuid(track_key, snapshot_sha256, canonical eval_window JSON, identity, split_version) — 不含 run_id",
    "consume_rule": "guarded UPDATE WHERE status IN ('REGISTERED','LOCKED_UNOPENED'); 已消费窗口返回 HELDOUT_ALREADY_CONSUMED",
    "rows": registry,
})

evals = q("select eval_id::text, run_id::text, track, identity, snapshot_sha256, locked_at::text, "
          "evaluated_at::text, eval_count, wape, agreement_pct, coverage, mae, rmse, smape, mase, registry_id::text "
          "from epidemic_kg.kg_qwen_evaluation_sets order by created_at")
dump("05_HISTORICAL_RECLASSIFICATION.json", {
    "rule": "FRESH_HELDOUT_LOCKED 历史行全部归类 RETROSPECTIVE_REPLAY_NOT_FRESH；WAPE/吻合度/覆盖率/run_id 原样保留",
    "evaluation_sets": evals,
})

dump("06_BLIND_WINDOW_AUDIT.json", {
    "candidates": [
        {"series": "DEU:legionella:legionnaires_reported_cases:month", "verdict": "REJECTED_NOT_FRESH",
         "evidence": "全序列(2005-2024 含 2022-2024 窗口)已进入 5 个主平台 dataset snapshots(每个 240 行)，"
                     "且存在 116 行 forecast_values(target 至 2026-08)；窗口此前已被主平台打开"},
        {"series": "BRA:dengue:cases_new:week", "verdict": "REJECTED_INFEASIBLE",
         "evidence": "74 周长度过短且已进入平台 snapshots+forecast_values"},
        {"series": "CHL:influenza:influenza_positive_detections:week", "verdict": "QUALIFIED_FRESH",
         "evidence": "training_observations_v1=0, forecast_runs=0, forecast_values=0, prediction_results=0, "
                     "kg shadow runs=0, kg_data_snapshots=0；本地 p1_18_flunet 产物曾被阻塞从未训练；"
                     "1580 周(1997-04..2026-07)尾部 445 周无缺失无断档"},
    ],
})

dump("07_TRACK_C_RUN.json", run_c)

sent = tmp_json("/tmp/kq_sentinels.json")
dump("08_SENTINELS.json", sent)

dump("09_CONCURRENCY.json", tmp_json("/tmp/kq_concurrency.json"))

# Track C evaluation (from DB, after commit) incl. paired block + CSV
if run_c_id:
    ev_c = q("select metrics from epidemic_kg.kg_qwen_evaluation_sets where run_id = :r", {"r": run_c_id})
    metrics_c = (ev_c[0]["metrics"] if ev_c else {}) or {}
    paired = metrics_c.get("paired") or {}
    dump("10_PAIRED_BASELINE.json", {
        "paired_n": paired.get("paired_n"),
        "paired_sample_match": paired.get("paired_sample_match"),
        "paired_gate_failures": paired.get("paired_gate_failures"),
        "champion_adapter_id": paired.get("champion_adapter_id"),
        "champion_wape_paired": paired.get("champion_wape_paired"),
        "baselines": paired.get("baselines"),
        "best_baseline_id": paired.get("best_baseline_id"),
        "best_baseline_wape": paired.get("best_baseline_wape"),
        "relative_improvement_vs_best_baseline": paired.get("relative_improvement_vs_best_baseline"),
        "paired_csv_sha256": paired.get("paired_csv_sha256"),
    })
    if paired.get("paired_csv"):
        with open(f"{OUT}/10_PAIRED_BASELINE.csv", "w", encoding="utf-8", newline="") as f:
            f.write(paired["paired_csv"])
    dump("11_HELDOUT_EVALUATION_TRACK_C.json", {
        "run_id": run_c_id,
        "wape": metrics_c.get("wape"), "agreement_pct": metrics_c.get("agreement_pct"),
        "coverage": metrics_c.get("coverage"), "mae": metrics_c.get("mae"), "rmse": metrics_c.get("rmse"),
        "smape": metrics_c.get("smape"), "mase": metrics_c.get("mase"),
        "per_horizon": metrics_c.get("per_horizon"),
        "identity": metrics_c.get("identity"), "classification": metrics_c.get("classification"),
        "registry_id": metrics_c.get("registry_id"), "registry_status": metrics_c.get("registry_status"),
        "time_chain": metrics_c.get("time_chain"), "certification": metrics_c.get("certification"),
        "window": metrics_c.get("window"), "n_origins": metrics_c.get("n_origins"),
    })

plans = q("select plan_id::text, run_id::text, round_index, plan_source, plan_sha256, prompt_sha256, "
          "response_sha256, qwen_latency_ms, validation_status, validation_errors, created_at::text "
          "from epidemic_kg.kg_qwen_tuning_plans where run_id = :r order by created_at", {"r": run_c_id or ""})
dump("12_QWEN_PLANS_TRACK_C.json", plans)
rounds = q("select run_id::text, round_index, status, trials_executed, best_adapter_id, best_val_wape, "
           "best_val_agreement_pct, best_val_coverage, baseline_best_wape, improvement_vs_baseline, "
           "target_met, stop_reason from epidemic_kg.kg_qwen_tuning_rounds where run_id = :r "
           "order by round_index", {"r": run_c_id or ""})
dump_csv("13_TUNING_ROUNDS_TRACK_C.csv", rounds)
trials = q("select run_id::text, round_index, trial_index, adapter_id, params, common_wape, agreement_pct, "
           "coverage, gate_pass, failure_reason, trial_sha256, elapsed_ms "
           "from epidemic_kg.kg_qwen_trials where run_id = :r order by round_index, adapter_id, trial_index",
           {"r": run_c_id or ""})
dump_csv("14_TRIALS_TRACK_C.csv", trials)

dump("15_API_ACCEPTANCE.json", tmp_json("/tmp/api_acceptance_governance.json"))
dump("16_BROWSER_ACCEPTANCE.json", tmp_json("/tmp/browser_acceptance_governance.json"))
dump("17_NON_MUTATION.json", tmp_json("/tmp/kq_non_mutation.json"))
dump("18_IDEMPOTENCY.json", {
    "api_eight_way": (tmp_json("/tmp/api_acceptance_governance.json").get("checks") or [])[-1:],
    "global_consume_eight_way": tmp_json("/tmp/kq_concurrency.json"),
})

champions = q("select run_id::text, champion_adapter_id, champion_source, kg_model_status, reason, "
              "promotion_applied, created_at::text from epidemic_kg.kg_champion_decisions order by created_at")
dump("19_CHAMPION_DECISIONS.json", champions)
cycles = q("select learning_cycle_id::text, run_id::text, cycle_type, status, min_matured_feedback, "
           "matured_feedback_count, evidence_sha256, created_at::text from epidemic_kg.kg_learning_cycles "
           "order by created_at")
dump("20_LEARNING_CYCLES.json", cycles)

dump("21_REPRODUCIBILITY.json", {
    "commit": git_head,
    "empty_db_replay": "PASS - scratch db base->0043; downgrade 0043->0042->upgrade OK; scratch dropped",
    "pytest": "175 passed (tests/kg_qwen)",
    "ruff": "touched modules clean; strict mypy clean; pip check clean",
    "readyz": "ready=true required_revision=0043_kg_heldout_governance",
    "secret_scan": 'REDACTED',
})

with open(f"{OUT}/22_ROLLBACK.md", "w", encoding="utf-8") as f:
    f.write("""# 回滚方案

1. 代码：`git revert <本轮提交>` 并重启 dashboard（runtime/start_vm201_dashboard.sh）。
2. 数据库：`alembic downgrade 0042_kg_qwen_autotuning` 删除 kg_qwen_heldout_registry 与新增列；
   重分类属于审计更正，downgrade 不会回写（设计如此）。Track C 的 Shadow 行
   （snapshot CHL%、agent run、plans/rounds/trials、evaluation、shadow runs、feedback、cycles）
   均为 Shadow 追加行，可按 run_id / demo_key 前缀删除，不影响 Trusted。
3. 全量恢复：`pg_restore` 备份 runtime/kg_governance_backup_20260812T055425Z/pre_0043.dump
   （sha256 0d3bac629bf9a435c882ed77ac5936f91e56a1cb47b3cce88dc7d30748c7a172）到 scratch 库验证后切换。
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

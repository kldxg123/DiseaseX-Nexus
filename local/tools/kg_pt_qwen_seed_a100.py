#!/usr/bin/env python3
"""Seed the kg_qwen autotuning + held-out governance demo layer (0042/0043)
into the A100 epidemic_kg schema from the vm201 demo exports.

Sources:
- reports/kg_qwen_autotuning_v1_20260812T035231Z/        (Tracks A/B: plans, rounds, trials, evals)
- .codex_tmp/kg_gov_zip/kg_qwen_heldout_governance_repair_20260812T070536Z/
  (0043 post-repair state: registry, reclassified evaluation sets, Track C run)

Strategy: keep every exported primary key / content hash verbatim (run_id,
plan_id, eval_id, registry_id, trial_sha256, ...) so the pages show exactly the
vm201 demo content; derive the few missing surrogate ids with the same
stable_uuid formulas the pipeline uses (namespace c87f8a95-...). Track snapshots
are content-addressed: AUT/FIN are re-frozen from the live view via the deployed
kg_pt code (hashes verified to equal the vm201 exports); CHL (Track C) is seeded
from the exported snapshot hash with rows loaded from epidemic.normalized_observations
(A100 observation ids differ from vm201, so the stored snapshot_sha256 keeps the
exported value).

The kg_pt parameter-tuning demo (0041 + 0040 ledger) is NOT seeded here: it is
regenerated deterministically on A100 via execute_pt_demo (see
docs/kg_pt_qwen_seed_notes or the companion run in this task).

Idempotent: every insert is ON CONFLICT DO NOTHING with deterministic ids.

Usage (repo root, local .venv python):
    .venv/Scripts/python tools/kg_pt_qwen_seed_a100.py              # seed + verify
    .venv/Scripts/python tools/kg_pt_qwen_seed_a100.py --build-only # payload stats only
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
QWEN_DIR = ROOT / "reports" / "kg_qwen_autotuning_v1_20260812T035231Z"
GOV_DIR = ROOT / ".codex_tmp" / "kg_gov_zip" / "kg_qwen_heldout_governance_repair_20260812T070536Z"
PT_DIR = ROOT / "reports" / "kg_parameter_tuning_self_learning_demo_20260811T040516Z"
MIG_MODULE = ROOT / "tools" / "migrate_epidemic_to_a100.py"

NS = uuid.UUID("c87f8a95-7d2c-4f3e-9b1a-2e5d6a8b0c1f")  # kg_r3c.execution / kg_qwen
TRACK_A_KEY = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B_KEY = "FIN:hantavirus:puumala_virus_reports:week"
TRACK_C_KEY = "CHL:influenza:influenza_positive_detections:week"
TRACK_C_IDEM = "kq-trackC-20260812"  # vm201 key not exported; cosmetic only
TRACK_C_SHADOW_RUN = "a9b105a5-257a-5933-83ef-98617e2f44bb"
TRACK_C_SNAPSHOT_SHA = "9f43a5b6e58df7f0cd887868526bfe4fd9c3cef9510334f61ed530a5626b3135"
DEMO_RECOMMENDATION_ID = "c219de41-ea2e-58d9-86cd-339b03213401"
VM201_PT_SHADOW_RUN = "bc72b5bd-d26a-5b35-847a-8b7c7ea863c9"  # absent on A100; skipped

QWEN_MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
QWEN_RUNTIME_LABEL = "transformers-5.8.1+torch-2.12.0-cpu"
KQ_SEED = 20260811
KQ_IMPL_VERSION = "kg_qwen.v1"


def stable_uuid(kind: str, key: str) -> str:
    return str(uuid.uuid5(NS, f"{kind}:{key}"))


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_migrate_module() -> Any:
    spec = importlib.util.spec_from_file_location("mig", MIG_MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mig"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload() -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []

    tracks = _json_file(QWEN_DIR / "03_TRACKS.json")
    plans_ab = _json_file(QWEN_DIR / "04_QWEN_PLANS.json")
    rounds_ab = list(csv.DictReader((QWEN_DIR / "06_TUNING_ROUNDS.csv").open(encoding="utf-8")))
    trials_ab = list(csv.DictReader((QWEN_DIR / "07_TRIALS.csv").open(encoding="utf-8")))

    registry = _json_file(GOV_DIR / "04_HELDOUT_REGISTRY.json")["rows"]
    eval_sets = _json_file(GOV_DIR / "05_HISTORICAL_RECLASSIFICATION.json")["evaluation_sets"]
    track_c_run = _json_file(GOV_DIR / "07_TRACK_C_RUN.json")
    eval_c = _json_file(GOV_DIR / "11_HELDOUT_EVALUATION_TRACK_C.json")
    plans_c = _json_file(GOV_DIR / "12_QWEN_PLANS_TRACK_C.json")
    if isinstance(plans_c, dict):
        plans_c = plans_c.get("plans", [])
    rounds_c = list(csv.DictReader((GOV_DIR / "13_TUNING_ROUNDS_TRACK_C.csv").open(encoding="utf-8")))
    trials_c = list(csv.DictReader((GOV_DIR / "14_TRIALS_TRACK_C.csv").open(encoding="utf-8")))
    decisions = _json_file(GOV_DIR / "19_CHAMPION_DECISIONS.json")
    cycles = _json_file(GOV_DIR / "20_LEARNING_CYCLES.json")
    if isinstance(decisions, dict):
        decisions = decisions.get("champion_decisions", [])
    if isinstance(cycles, dict):
        cycles = cycles.get("learning_cycles", [])

    eval_window_by_track: dict[str, Any] = {}
    for e in tracks["evaluation_sets"]:
        if e.get("eval_window"):
            eval_window_by_track.setdefault(e["track"], e["eval_window"])
    for e in eval_sets:
        if e.get("eval_window"):
            eval_window_by_track.setdefault(e["track"], e["eval_window"])
    eval_window_by_track.setdefault(TRACK_C_KEY, eval_c.get("window"))

    # --- agent runs: 7 exported + Track C synthesized from governance run summary ---
    agent_runs = [dict(r) for r in tracks["agent_runs"]]
    ref = agent_runs[0]
    agent_runs.append({
        "run_id": track_c_run["run_id"],
        "idempotency_key": TRACK_C_IDEM,
        "track_key": TRACK_C_KEY,
        "status": track_c_run["status"],
        "stop_reason": track_c_run["stop_reason"],
        "target_status": "TARGET_MET" if track_c_run["target_met"] else "NOT_MET",
        "qwen_model_id": QWEN_MODEL_ID,
        "qwen_model_fingerprint": ref.get("qwen_model_fingerprint"),
        "qwen_runtime": QWEN_RUNTIME_LABEL,
        "prompt_template_sha256": ref.get("prompt_template_sha256"),
        "snapshot_sha256": TRACK_C_SNAPSHOT_SHA,
        "split_sha256": None,
        "seed": KQ_SEED,
        "code_version": KQ_IMPL_VERSION,
        "plan_validator_version": ref.get("plan_validator_version"),
        "created_at": "2026-08-12 14:33:00+08",
        "finished_at": "2026-08-12 14:41:14.692063+08",
    })

    # --- shadow runs (one per agent run), ids verified against exported cycle/decision run_ids ---
    champion_by_run: dict[str, str] = {}
    for d in sorted(decisions, key=lambda x: (x["run_id"], x.get("created_at") or "")):
        champion_by_run.setdefault(d["run_id"], d["champion_adapter_id"])
    shadow_runs = []
    for r in agent_runs:
        if r["track_key"] == TRACK_C_KEY:
            shadow_id = TRACK_C_SHADOW_RUN
        else:
            shadow_id = stable_uuid("kq-shadow-run", f"kg-qwen-shadow:{r['idempotency_key']}")
        shadow_runs.append({
            "run_id": shadow_id,
            "run_key": f"kg-qwen:{r['track_key']}:{r['snapshot_sha256'][:12]}:{r['idempotency_key']}",
            "idempotency_key": f"kg-qwen:{r['idempotency_key']}",
            "track_key": r["track_key"],
            "snapshot_sha256": r["snapshot_sha256"],
            "split_sha256": r.get("split_sha256"),
            "champion_adapter": champion_by_run.get(shadow_id) or "seasonal_naive",
        })
    exported_shadow_ids = {c["run_id"] for c in cycles} | {d["run_id"] for d in decisions}
    known_other = {VM201_PT_SHADOW_RUN}  # vm201 PT run; not re-created on A100
    for sr in shadow_runs:
        if sr["run_id"] not in exported_shadow_ids:
            warnings.append(f"shadow run {sr['run_id'][:8]} not referenced by exports")

    # --- plans (13 A/B verbatim + 3 Track C with synthesized plan_json) ---
    plans = [dict(p) for p in plans_ab]
    for p in plans_c:
        plans.append({
            **p,
            "plan_json": {
                "plan_version": "seed-placeholder",
                "objective": "minimize common WAPE over H1-H4 validation origins",
                "rationale_zh": "该计划来自 vm201 治理修复演示（Track C），导出文件未包含 plan_json 原文，此为占位重建。",
                "seeded_from": "kg_qwen_heldout_governance_repair/12_QWEN_PLANS_TRACK_C.json",
            },
        })
        warnings.append(f"Track C plan {p['plan_id'][:8]}: plan_json synthesized (not in export)")

    # --- rounds (derive round_id via pipeline formula; best_trial_id not exported) ---
    rounds = []
    for row in list(rounds_ab) + list(rounds_c):
        rounds.append({
            "round_id": stable_uuid("kq-round", f"{row['run_id']}:{row['round_index']}"),
            "run_id": row["run_id"],
            "round_index": int(row["round_index"]),
            "status": row["status"],
            "trials_executed": int(row["trials_executed"]) if row["trials_executed"] else None,
            "best_adapter_id": row["best_adapter_id"] or None,
            "best_trial_id": None,
            "best_val_wape": float(row["best_val_wape"]) if row["best_val_wape"] else None,
            "best_val_agreement_pct": float(row["best_val_agreement_pct"]) if row["best_val_agreement_pct"] else None,
            "best_val_coverage": float(row["best_val_coverage"]) if row["best_val_coverage"] else None,
            "baseline_best_wape": float(row["baseline_best_wape"]) if row["baseline_best_wape"] else None,
            "improvement_vs_baseline": float(row["improvement_vs_baseline"]) if row["improvement_vs_baseline"] else None,
            "target_met": row["target_met"] == "True",
            "stop_reason": row["stop_reason"] or None,
        })

    # --- trials (derive trial_id + params_sha256) ---
    trials = []
    for row in list(trials_ab) + list(trials_c):
        params = json.loads(row["params"]) if row.get("params") else {}
        psource = row.get("parameter_source") or None
        trials.append({
            "trial_id": stable_uuid("kq-trial", f"{row['run_id']}:{row['trial_sha256']}"),
            "run_id": row["run_id"],
            "round_index": int(row["round_index"]),
            "trial_index": int(row["trial_index"]),
            "adapter_id": row["adapter_id"],
            "params": params,
            "params_sha256": sha256_json(params),
            "parameter_source": json.loads(psource) if psource else None,
            "origin_sha256s": None,
            "per_horizon_metrics": None,
            "common_wape": float(row["common_wape"]) if row.get("common_wape") else None,
            "agreement_pct": float(row["agreement_pct"]) if row.get("agreement_pct") else None,
            "coverage": float(row["coverage"]) if row.get("coverage") else None,
            "avg_interval_width": float(row["avg_interval_width"]) if row.get("avg_interval_width") else None,
            "complexity_penalty": float(row["complexity_penalty"]) if row.get("complexity_penalty") else None,
            "objective_value": float(row["objective_value"]) if row.get("objective_value") else None,
            "gates": None,
            "gate_pass": row["gate_pass"] == "True",
            "failure_reason": row.get("failure_reason") or None,
            "artifact_sha256": row.get("artifact_sha256") or None,
            "dataset_sha256": row.get("dataset_sha256") or None,
            "trial_sha256": row["trial_sha256"],
            "elapsed_ms": int(row["elapsed_ms"]) if row.get("elapsed_ms") else None,
        })

    # --- evaluation sets (post-repair identities; metrics only available for Track C) ---
    evaluations = []
    for e in eval_sets:
        metrics = None
        if e["track"].startswith("CHL"):
            metrics = {k: eval_c[k] for k in ("per_horizon", "classification", "time_chain",
                                              "certification", "n_origins") if k in eval_c}
        evaluations.append({**e, "eval_window": e.get("eval_window") or eval_window_by_track.get(e["track"]),
                            "metrics": metrics})
    if any(not e.get("eval_window") for e in evaluations):
        warnings.append("some evaluation_sets lack eval_window")

    # --- held-out registry (synthesize the two NN jsonb columns) ---
    registry_rows = []
    for r in registry:
        registry_rows.append({
            **r,
            "eval_window": eval_window_by_track.get(r["track_key"]),
            "global_identity": {
                "track_key": r["track_key"], "identity": r["identity"],
                "snapshot_sha256": r["snapshot_sha256"], "split_version": r["split_version"],
                "eval_window": eval_window_by_track.get(r["track_key"]),
                "seeded_from": "kg_qwen_heldout_governance_repair/04_HELDOUT_REGISTRY.json",
            },
        })

    # --- learning cycles + champion decisions for the 8 qwen shadow runs ---
    qwen_shadow_ids = {sr["run_id"] for sr in shadow_runs}
    cycles_out = []
    for c in cycles:
        if c["run_id"] not in qwen_shadow_ids:
            continue  # older dual-loop / vm201-PT runs: A100 has its own equivalents
        cycles_out.append({
            **c,
            "recommendation_id": None,
            "evidence": {"seeded_from": "vm201 export 13_LEARNING_CYCLES/20_LEARNING_CYCLES",
                         "note": "evidence jsonb not included in the export; placeholder",
                         "evidence_sha256": c["evidence_sha256"]},
        })
    decisions_out = []
    cycles_by_run: dict[str, list[dict[str, Any]]] = {}
    for c in cycles_out:
        cycles_by_run.setdefault(c["run_id"], []).append(c)
    for lst in cycles_by_run.values():
        lst.sort(key=lambda c: c.get("created_at") or "")
    for d in decisions:
        if d["run_id"] not in cycles_by_run:
            continue
        cand = cycles_by_run[d["run_id"]]
        before = [c for c in cand if (c.get("created_at") or "") <= (d.get("created_at") or "")]
        cycle = (before or cand)[-1] if before else cand[0]
        if len(cand) > 1 and not before:
            cycle = cand[0]
        decisions_out.append({
            "decision_id": stable_uuid("champion-decision", cycle["learning_cycle_id"]),
            "learning_cycle_id": cycle["learning_cycle_id"],
            **d,
            "decided_by": "SYSTEM_BACKTEST",
        })

    payload = {
        "agent_runs": agent_runs,
        "shadow_runs": shadow_runs,
        "plans": plans,
        "rounds": rounds,
        "trials": trials,
        "evaluations": evaluations,
        "registry": registry_rows,
        "learning_cycles": cycles_out,
        "champion_decisions": decisions_out,
        "track_c_snapshot": {
            "snapshot_id": stable_uuid("pt-snapshot", TRACK_C_SNAPSHOT_SHA),
            "demo_key": TRACK_C_KEY,
            "snapshot_sha256": TRACK_C_SNAPSHOT_SHA,
        },
        "tracks": {
            "A": TRACK_A_KEY, "B": TRACK_B_KEY, "C": TRACK_C_KEY,
            "expected_snapshot_sha": {
                TRACK_A_KEY: "201b2add5c2999fe707f74316453429979f534387a079f466765a2917eefbc6f",
                TRACK_B_KEY: "a3add2276e958b607ac666847f0de36d79995745c026e5538427d622946ee2d6",
                TRACK_C_KEY: TRACK_C_SNAPSHOT_SHA,
            },
        },
    }
    return payload, warnings


REMOTE_PY = r'''
import json, os, sys, hashlib, uuid
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.expanduser("~/autodl-tmp/epidemic-dashboard-runtime/src"))
import psycopg

seed = json.load(open(os.path.expanduser("~/autodl-tmp/_kg_qwen_seed_payload.json"), encoding="utf-8"))
pw = os.environ["POSTGRES_ADMIN_PASSWORD"]
parts = urlsplit(os.environ["MIGRATION_DATABASE_URL"])
netloc = "epidemicdb:" + pw + "@" + (parts.hostname or "127.0.0.1") + ":" + str(parts.port or 5432)
dsn = urlunsplit(("postgresql", netloc, parts.path, parts.query, parts.fragment))

def _san(t):
    return t.replace(pw, "***").replace(dsn, "<dsn>")

def _hook(t, v, tb):
    print("SEED_FATAL: " + _san(t.__name__ + ": " + str(v)))
    sys.exit(4)

sys.excepthook = _hook
conn = psycopg.connect(dsn)
cur = conn.cursor()
S = "epidemic_kg"
report = {"notes": []}

TABLES = ("kg_pt_demo_snapshots", "kg_pt_demo_snapshot_rows", "kg_forecast_shadow_runs",
          "kg_qwen_agent_runs", "kg_qwen_tuning_plans", "kg_qwen_tuning_rounds",
          "kg_qwen_trials", "kg_qwen_evaluation_sets", "kg_qwen_heldout_registry",
          "kg_learning_cycles", "kg_champion_decisions")
def count(tb):
    cur.execute("select count(*) from epidemic_kg." + tb)
    return cur.fetchone()[0]
before = {t: count(t) for t in TABLES}

from epidemic_core.kg_pt import pipeline as pt
from epidemic_core.kg_qwen import pipeline as qp

TRACK_B_KEY = seed["tracks"]["B"]
TRACK_C_KEY = seed["tracks"]["C"]
expected_sha = seed["tracks"]["expected_snapshot_sha"]

# ---- 1. track snapshots ----
# Track A snapshot already exists (PT demo run); ensure Track B from the live view.
snap_b = pt.ensure_demo_snapshot(conn, demo_key=TRACK_B_KEY)
report["track_b_snapshot"] = {"snapshot_id": snap_b["snapshot_id"],
                              "snapshot_sha256": snap_b["snapshot_sha256"],
                              "matches_vm201": snap_b["snapshot_sha256"] == expected_sha[TRACK_B_KEY]}
# Track C: seed from the exported hash; rows loaded from normalized_observations.
tc = seed["track_c_snapshot"]
cur.execute("select snapshot_id::text from epidemic_kg.kg_pt_demo_snapshots where snapshot_sha256=%s",
            (tc["snapshot_sha256"],))
row = cur.fetchone()
if row:
    report["notes"].append("track C snapshot already present: " + row[0])
else:
    rows_all = qp._load_normalized_rows(conn, qp.TRACK_C["source_override"])
    rows = rows_all[-qp.TRACK_C["total_n"]:]
    from epidemic_core.forecast_model_kg.dual_loop import dataset_sha256
    from epidemic_core.kg_r3c.execution import sha256_json
    country, disease, indicator, frequency = TRACK_C_KEY.split(":")
    payload_rows = pt.snapshot_rows_payload(rows)
    cur.execute("""insert into epidemic_kg.kg_pt_demo_snapshots
        (snapshot_id, demo_key, country, disease, indicator, frequency, source_table,
         n_obs, first_period, last_period, data_through, revision_max, observation_keys,
         missing_periods, missing_semantics, dataset_sha256, snapshot_sha256)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)
        on conflict do nothing""",
        (tc["snapshot_id"], TRACK_C_KEY, country, disease, indicator, frequency,
         "epidemic.normalized_observations", len(rows), rows[0].period_start, rows[-1].period_start,
         rows[-1].period_start, max(r.revision for r in rows),
         json.dumps([r.observation_id for r in rows], default=str), "[]",
         "MISSING_VALUE_KEPT_NULL_NEVER_IMPUTED",
         dataset_sha256(rows, country, disease, indicator, frequency), tc["snapshot_sha256"]))
    for p in payload_rows:
        cur.execute("""insert into epidemic_kg.kg_pt_demo_snapshot_rows
            (snapshot_id, row_index, period_start, period_end, value_numeric,
             observation_id, revision, row_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
            (tc["snapshot_id"], p["row_index"], p["period_start"], p["period_end"],
             p["value_numeric"], p["observation_id"], p["revision"], sha256_json(p)))
    report["notes"].append("track C snapshot seeded: %d rows" % len(rows))

# snapshot metadata lookup for shadow-run profile/data_through
cur.execute("select snapshot_sha256, snapshot_id::text, country, disease, indicator, frequency,"
            " dataset_sha256, last_period::text from epidemic_kg.kg_pt_demo_snapshots")
snaps = {r[0]: r for r in cur.fetchall()}

# ---- 2. shadow runs (one per agent run) ----
for sr in seed["shadow_runs"]:
    snap = snaps[sr["snapshot_sha256"]]
    profile = {"kg_qwen": True, "track_key": sr["track_key"],
               "snapshot_sha256": sr["snapshot_sha256"], "dataset_sha256": snap[6],
               "split_sha256": sr["split_sha256"], "recommendation_id": None,
               "champion_adapter": sr["champion_adapter"], "impl_version": "kg_qwen.v1",
               "note": "champion of record lives in kg_champion_decisions; Shadow only",
               "seeded_from": "vm201 kg_qwen demo export"}
    cur.execute("""insert into epidemic_kg.kg_forecast_shadow_runs
        (run_id, run_key, idempotency_key, mode, status, disease_slug, country_alpha3,
         metric_code, cadence, data_through, model_name, fallback_reason,
         promotion_eligible, active_pointer_changed, trusted_rows_changed, profile, selection_trace)
        values (%s,%s,%s,'SHADOW','COMPLETED',%s,%s,%s,%s,%s,%s,NULL,false,false,0,%s::jsonb,%s::jsonb)
        on conflict do nothing""",
        (sr["run_id"], sr["run_key"], sr["idempotency_key"], snap[3], snap[2], snap[4], snap[5],
         snap[7], sr["champion_adapter"], json.dumps(profile, default=str),
         json.dumps({"shadow_notice": "SHADOW_ONLY_NO_PROMOTION", "replay": "DEMO_HISTORICAL_REPLAY"})))

# ---- 3. agent runs ----
for r in seed["agent_runs"]:
    snap = snaps.get(r["snapshot_sha256"])
    cur.execute("""insert into epidemic_kg.kg_qwen_agent_runs
        (run_id, idempotency_key, track_key, status, stop_reason, target_status,
         qwen_model_id, qwen_model_fingerprint, qwen_runtime, prompt_template_sha256,
         snapshot_id, snapshot_sha256, split_sha256, eval_window, budgets, seed,
         code_version, plan_validator_version, created_at, finished_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NULL,%s,%s,%s,%s,%s)
        on conflict do nothing""",
        (r["run_id"], r["idempotency_key"], r["track_key"], r["status"], r["stop_reason"],
         r["target_status"], r["qwen_model_id"], r["qwen_model_fingerprint"], r["qwen_runtime"],
         r["prompt_template_sha256"], snap[1] if snap else None, r["snapshot_sha256"],
         r["split_sha256"],
         json.dumps({"window": "see kg_qwen_heldout_registry / evaluation_sets",
                     "seeded_from": "vm201 export"}), r["seed"], r["code_version"],
         r["plan_validator_version"], r["created_at"], r["finished_at"]))

# ---- 4. plans ----
for p in seed["plans"]:
    cur.execute("""insert into epidemic_kg.kg_qwen_tuning_plans
        (plan_id, run_id, round_index, plan_source, plan_json, plan_sha256, prompt_sha256,
         response_sha256, qwen_latency_ms, validation_status, validation_errors, created_at)
        values (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb,%s)
        on conflict do nothing""",
        (p["plan_id"], p["run_id"], p["round_index"], p["plan_source"],
         json.dumps(p["plan_json"], ensure_ascii=False), p["plan_sha256"], p["prompt_sha256"],
         p["response_sha256"], p["qwen_latency_ms"], p["validation_status"],
         json.dumps(p.get("validation_errors") or [], ensure_ascii=False), p["created_at"]))

# ---- 5. rounds ----
for r in seed["rounds"]:
    cur.execute("""insert into epidemic_kg.kg_qwen_tuning_rounds
        (round_id, run_id, round_index, status, trials_executed, best_adapter_id, best_trial_id,
         best_val_wape, best_val_agreement_pct, best_val_coverage, baseline_best_wape,
         improvement_vs_baseline, target_met, stop_reason)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict do nothing""",
        (r["round_id"], r["run_id"], r["round_index"], r["status"], r["trials_executed"],
         r["best_adapter_id"], r["best_trial_id"], r["best_val_wape"],
         r["best_val_agreement_pct"], r["best_val_coverage"], r["baseline_best_wape"],
         r["improvement_vs_baseline"], r["target_met"], r["stop_reason"]))

# ---- 6. trials ----
for t in seed["trials"]:
    cur.execute("""insert into epidemic_kg.kg_qwen_trials
        (trial_id, run_id, round_index, trial_index, adapter_id, params, params_sha256,
         parameter_source, origin_sha256s, per_horizon_metrics, common_wape, agreement_pct,
         coverage, avg_interval_width, complexity_penalty, objective_value, gates, gate_pass,
         failure_reason, artifact_sha256, dataset_sha256, trial_sha256, elapsed_ms)
        values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,
                %s::jsonb,%s,%s,%s,%s,%s,%s)
        on conflict do nothing""",
        (t["trial_id"], t["run_id"], t["round_index"], t["trial_index"], t["adapter_id"],
         json.dumps(t["params"], ensure_ascii=False), t["params_sha256"],
         json.dumps(t["parameter_source"], ensure_ascii=False) if t["parameter_source"] is not None else None,
         None, None, t["common_wape"], t["agreement_pct"], t["coverage"],
         t["avg_interval_width"], t["complexity_penalty"], t["objective_value"], None,
         t["gate_pass"], t["failure_reason"], t["artifact_sha256"], t["dataset_sha256"],
         t["trial_sha256"], t["elapsed_ms"]))

# ---- 7. held-out registry (before evaluation_sets: registry_id FK) ----
for r in seed["registry"]:
    cur.execute("""insert into epidemic_kg.kg_qwen_heldout_registry
        (registry_id, global_sha256, global_identity, track_key, identity, snapshot_sha256,
         eval_window, split_version, evaluator_version, status, registered_at, locked_at,
         first_qwen_started_at, tuning_finished_at, consumed_at, consumer_run_id)
        values (%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict do nothing""",
        (r["registry_id"], r["global_sha256"], json.dumps(r["global_identity"], ensure_ascii=False),
         r["track_key"], r["identity"], r["snapshot_sha256"],
         json.dumps(r["eval_window"], ensure_ascii=False), r["split_version"],
         r["evaluator_version"], r["status"], r["registered_at"], r["locked_at"],
         r["first_qwen_started_at"], r["tuning_finished_at"], r["consumed_at"],
         r["consumer_run_id"]))

# ---- 8. evaluation sets ----
for e in seed["evaluations"]:
    cur.execute("""insert into epidemic_kg.kg_qwen_evaluation_sets
        (eval_id, run_id, track, identity, snapshot_sha256, eval_window, locked_at,
         evaluated_at, eval_count, metrics, wape, agreement_pct, coverage, mae, rmse,
         smape, mase, registry_id)
        values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict do nothing""",
        (e["eval_id"], e["run_id"], e["track"], e["identity"], e["snapshot_sha256"],
         json.dumps(e["eval_window"], ensure_ascii=False), e["locked_at"], e["evaluated_at"],
         e["eval_count"], json.dumps(e["metrics"], ensure_ascii=False) if e["metrics"] else None,
         e["wape"], e["agreement_pct"], e["coverage"], e["mae"], e["rmse"], e["smape"],
         e["mase"], e.get("registry_id")))

# ---- 9. learning cycles + champion decisions (qwen shadow runs only) ----
for c in seed["learning_cycles"]:
    cur.execute("""insert into epidemic_kg.kg_learning_cycles
        (learning_cycle_id, run_id, recommendation_id, cycle_type, status,
         min_matured_feedback, matured_feedback_count, evidence, evidence_sha256, created_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        on conflict do nothing""",
        (c["learning_cycle_id"], c["run_id"], c["recommendation_id"], c["cycle_type"],
         c["status"], c["min_matured_feedback"], c["matured_feedback_count"],
         json.dumps(c["evidence"], ensure_ascii=False), c["evidence_sha256"], c["created_at"]))
    report["notes"].append("cycle %s rowcount=%s" % (c["learning_cycle_id"][:8], cur.rowcount))
cur.execute("select learning_cycle_id::text from epidemic_kg.kg_learning_cycles")
present_cycles = {r[0] for r in cur.fetchall()}
for d in seed["champion_decisions"]:
    if d["learning_cycle_id"] not in present_cycles:
        report["notes"].append("SKIP decision %s: cycle %s absent"
                               % (d["decision_id"][:8], d["learning_cycle_id"][:8]))
        continue
    cur.execute("""insert into epidemic_kg.kg_champion_decisions
        (decision_id, learning_cycle_id, run_id, champion_adapter_id, champion_source,
         kg_model_status, reason, decided_by, promotion_applied, created_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict do nothing""",
        (d["decision_id"], d["learning_cycle_id"], d["run_id"], d["champion_adapter_id"],
         d["champion_source"], d["kg_model_status"], d["reason"], d["decided_by"],
         d["promotion_applied"], d["created_at"]))

conn.commit()

after = {t: count(t) for t in TABLES}
report["counts"] = after
report["inserted"] = {t: after[t] - before[t] for t in TABLES}

# ---- 10. verification: replay the page read queries as the read-only role ----
conn.rollback()
cur.execute("set role epidemic_dashboard_ro")
verify = {}
for key, name in ((seed["tracks"]["A"], "A"), (seed["tracks"]["B"], "B"), (seed["tracks"]["C"], "C")):
    cur.execute("""select run_id::text, status, stop_reason from epidemic_kg.kg_qwen_agent_runs
                   where track_key=%s order by created_at desc limit 1""", (key,))
    run = cur.fetchone()
    entry = {"latest_run": list(map(str, run)) if run else None}
    if run:
        rid = run[0]
        for label, sql in (
            ("plans", "select count(*) from epidemic_kg.kg_qwen_tuning_plans where run_id=%s"),
            ("rounds", "select count(*) from epidemic_kg.kg_qwen_tuning_rounds where run_id=%s"),
            ("trials", "select count(*) from epidemic_kg.kg_qwen_trials where run_id=%s"),
            ("evals", "select count(*) from epidemic_kg.kg_qwen_evaluation_sets where run_id=%s"),
        ):
            cur.execute(sql, (rid,))
            entry[label] = cur.fetchone()[0]
        cur.execute("""select count(*) from epidemic_kg.kg_forecast_shadow_runs
                       where idempotency_key=%s""", ("kg-qwen:" + [r for r in seed["agent_runs"] if r["run_id"] == rid][0]["idempotency_key"],))
        entry["shadow_run_linked"] = cur.fetchone()[0]
    cur.execute("""select status from epidemic_kg.kg_qwen_heldout_registry
                   where track_key=%s order by registered_at""", (key,))
    entry["registry"] = [r[0] for r in cur.fetchall()]
    cur.execute("""select count(*) from epidemic_kg.kg_pt_demo_snapshots where demo_key=%s""", (key,))
    entry["snapshots"] = cur.fetchone()[0]
    verify["track_" + name] = entry
# PT demo overview read
cur.execute("""select snapshot_id::text, n_obs, snapshot_sha256 from epidemic_kg.kg_pt_demo_snapshots
               where demo_key=%s order by created_at desc limit 1""", (seed["tracks"]["A"],))
verify["pt_snapshot"] = list(map(str, cur.fetchone()))
cur.execute("""select tuning_run_id::text, cycle_index, status from epidemic_kg.kg_pt_tuning_runs
               where snapshot_sha256=%s order by cycle_index""", (verify["pt_snapshot"][2],))
verify["pt_tuning_runs"] = [list(map(str, r)) for r in cur.fetchall()]
cur.execute("""select adapter_id, is_tuned_model, wape, agreement_pct from epidemic_kg.kg_pt_test_evaluations
               where tuning_run_id=%s order by adapter_id""", (verify["pt_tuning_runs"][0][0],))
verify["pt_test_evaluations"] = [list(map(str, r)) for r in cur.fetchall()]
report["verify"] = verify
conn.close()
print("SEED_RESULT_BEGIN")
print(json.dumps(report, ensure_ascii=False, default=str))
print("SEED_RESULT_END")
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true",
                        help="assemble and validate the seed payload without SSH")
    args = parser.parse_args()

    payload, warnings = build_payload()
    print(f"payload: agent_runs={len(payload['agent_runs'])} shadow_runs={len(payload['shadow_runs'])} "
          f"plans={len(payload['plans'])} rounds={len(payload['rounds'])} trials={len(payload['trials'])} "
          f"evaluations={len(payload['evaluations'])} registry={len(payload['registry'])} "
          f"cycles={len(payload['learning_cycles'])} decisions={len(payload['champion_decisions'])}")
    for w in warnings:
        print(f"WARN: {w}")
    if args.build_only:
        return 0

    mig = load_migrate_module()
    creds = mig.credentials(mig.TARGET_INFO)
    client, _gate = mig.connect_with_gate("target", creds)
    try:
        local_payload = Path(os.environ.get("TEMP", ".")) / "_kg_qwen_seed_payload.json"
        local_payload.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        rc, home, _ = mig.run(client, "echo $HOME", timeout=30)
        home = home.strip().splitlines()[-1] if home.strip() else f"/home/{creds.user}"
        sftp = client.open_sftp()
        sftp.put(str(local_payload), f"{home}/autodl-tmp/_kg_qwen_seed_payload.json")
        sftp.close()
        local_payload.unlink()

        b64 = base64.b64encode(REMOTE_PY.encode("utf-8")).decode("ascii")
        py = "$HOME/autodl-tmp/conda/envs/epidemic-core/bin/python"
        cmd = (
            "source $HOME/autodl-tmp/epidemic-openclaw/scripts/database/_env.sh && "
            "set -a && source $HOME/autodl-tmp/epidemic-openclaw/.secrets/postgres.env && set +a && "
            f"echo {b64} | base64 -d | {py}"
        )
        rc, out, err = mig.run(client, cmd, timeout=900)
        if "SEED_RESULT_BEGIN" in out:
            result = out.split("SEED_RESULT_BEGIN", 1)[1].split("SEED_RESULT_END", 1)[0]
            print(json.dumps(json.loads(result), ensure_ascii=False, indent=1))
        else:
            print("REMOTE OUTPUT (no result marker):")
            print(out[-3000:])
        if rc != 0:
            print(f"remote exited rc={rc}; stderr tail:")
            print(err[-1500:])
            return 2
        if err:
            print("remote stderr (non-fatal):")
            print(err[-800:])
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

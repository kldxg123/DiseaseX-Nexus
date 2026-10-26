"""Replay the kg_pt demo closed loop on a restored pre-round dump and compare anchors.

Runs from the CLEAN CLONE (/tmp/openclaw-repro/src) against scratch DB epidemic_repro_full.
Prints deterministic anchors; must match production values.
"""
from __future__ import annotations

import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/tmp/openclaw-repro/src")

from epidemic_core.kg_pt import pipeline  # noqa: E402

SOCK = "USER_HOME/projects/openclaw/runtime/postgres-run"

EXPECTED = {
    "shadow_run_id": "bc72b5bd-d26a-5b35-847a-8b7c7ea863c9",
    "tuning_run_id": "0e9a5ba1-5aa9-5f72-af99-bca75b88bdc9",
    "tuning_run_id_cycle2": "4f3d314d-b977-5a4b-aca6-778fe16b4805",
    "best_trial_index": 8,
    "best_params": {
        "discount": 0.9636238856515807,
        "shape": 44.50880572450887,
        "prior_shape": 0.7284815514250284,
        "prior_rate": 12.415760843784513,
    },
    "tuned_test_wape": 0.413637404471545,
    "champion_adapter_id": "seasonal_naive",
}

conn = psycopg.connect(host=SOCK, port=55432, user="postgres", dbname="epidemic_repro_full", row_factory=dict_row)
result = pipeline.execute_pt_demo(conn, actor="repro_check", idempotency_key="pt-demo-repro:v1")
conn.commit()

print("status:", result.get("status"))
ok = True
for key in ("shadow_run_id", "tuning_run_id", "tuning_run_id_cycle2", "best_trial_index"):
    match = str(result.get(key)) == str(EXPECTED[key])
    ok = ok and match
    print(f"{key}: {result.get(key)}  match={match}")
bp = result.get("best_params") or {}
bp_match = all(abs(float(bp.get(k, -1)) - float(v)) < 1e-12 for k, v in EXPECTED["best_params"].items())
print("best_params:", bp, "match=", bp_match)
ok = ok and bp_match
ch = result.get("champion") or {}
tw = float(ch.get("tuned_common_wape") or -1)
wape_match = abs(tw - EXPECTED["tuned_test_wape"]) < 1e-9
print("tuned_test_wape:", tw, "match=", wape_match)
print("champion_adapter_id:", ch.get("champion_adapter_id"), "match=", ch.get("champion_adapter_id") == EXPECTED["champion_adapter_id"])
ok = ok and wape_match and ch.get("champion_adapter_id") == EXPECTED["champion_adapter_id"]
print("snapshot_sha256:", result.get("snapshot_sha256"))
print("feedback:", result.get("feedback"))
print("retrain:", result.get("retrain"))
print("REPRO_ANCHORS:", "MATCH" if ok else "MISMATCH")
sys.exit(0 if ok else 1)

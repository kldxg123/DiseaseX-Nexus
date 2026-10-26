import json
import sys
import time

sys.path.insert(0, "runtime/python-packages")
sys.path.insert(0, "src")

from epidemic_core.kg_qwen import TRACK_C_KEY
from epidemic_core.kg_qwen import pipeline as kq_pipeline
from epidemic_core.kg_r3c.dashboard_app import write_conn

IDEM = "kq-trackC-qwen1-20260812"

t0 = time.time()
print(f"TRACK_C_BLIND_RUN start key={IDEM} track={TRACK_C_KEY}", flush=True)
try:
    with write_conn() as conn:
        result = kq_pipeline.execute_autotuning(conn, actor="governance_repair", idempotency_key=IDEM, track_key=TRACK_C_KEY)
        conn.commit()
    out = {
        "ok": True,
        "elapsed_s": round(time.time() - t0, 1),
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "stop_reason": result.get("stop_reason"),
        "target_met": result.get("target_met"),
        "champion_adapter_id": result.get("champion_adapter_id"),
        "champion_source": result.get("champion_source"),
        "champion_params": result.get("champion_params"),
        "best_val_wape": result.get("best_val_wape"),
        "best_val_agreement_pct": result.get("best_val_agreement_pct"),
        "best_val_coverage": result.get("best_val_coverage"),
        "heldout_wape": result.get("heldout_wape"),
        "heldout_agreement_pct": result.get("heldout_agreement_pct"),
        "registry_id": result.get("registry_id"),
        "registry_status": result.get("registry_status"),
        "classification": result.get("classification"),
        "time_chain": result.get("time_chain"),
        "certification": result.get("certification"),
        "paired_summary": result.get("paired_summary"),
    }
except Exception as exc:
    out = {"ok": False, "elapsed_s": round(time.time() - t0, 1), "error": f"{type(exc).__name__}:{exc}"}

with open("/tmp/kq_trackC_result.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2, default=str)
print(json.dumps(out, ensure_ascii=False, default=str)[:1200], flush=True)
print("TRACK_C_DONE", flush=True)

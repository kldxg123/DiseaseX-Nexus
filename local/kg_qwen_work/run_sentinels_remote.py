import json
import sys
import time

sys.path.insert(0, "runtime/python-packages")
sys.path.insert(0, "src")

from epidemic_core.kg_pt import pipeline as pt_pipeline
from epidemic_core.kg_pt.split import make_split
from epidemic_core.kg_qwen import KQ_SEED, TRACK_A_KEY, TRACK_B_KEY, TRACK_C_KEY
from epidemic_core.kg_qwen import pipeline as kq_pipeline
from epidemic_core.kg_qwen import sentinels
from epidemic_core.kg_r3c.dashboard_app import write_conn

out = {}
# Full snapshot rows (including the hidden window) are mandatory: a sentinel
# suite over the truncated tuning window has an EMPTY hidden region and every
# leakage check would be vacuous (governance repair 0043).
for track_key in (TRACK_A_KEY, TRACK_B_KEY, TRACK_C_KEY):
    track = kq_pipeline._track(track_key)
    with write_conn() as conn:
        snap = conn.execute(
            "select snapshot_id::text, snapshot_sha256 from epidemic_kg.kg_pt_demo_snapshots "
            "where demo_key = %s order by created_at desc limit 1",
            (track_key,),
        ).fetchone()
        if not snap:
            out[track_key] = {"skipped": "NO_SNAPSHOT_YET"}
            print(track_key, "skipped (no snapshot)", flush=True)
            continue
        rows = pt_pipeline._load_frozen_rows(conn, snap["snapshot_id"])
    periods = [r.period_start for r in rows]
    tuning_n = track.get("tuning_n") or len(periods)
    # Split geometry identical to the pipeline (content-addressed over the
    # tuning window periods); rows stay FULL so _hidden() is non-empty.
    split = make_split(periods[:tuning_n], track["train_n"], track["val_n"], track["test_n"])
    t0 = time.time()
    results = sentinels.run_all_sentinels(
        track_key=track_key,
        frequency=track["frequency"],
        rows=rows,
        split=split,
        snapshot_sha256=snap["snapshot_sha256"],
        seed=KQ_SEED,
    )
    out[track_key] = {
        "elapsed_s": round(time.time() - t0, 1),
        "n_rows": len(rows),
        "hidden_rows": max(0, len(rows) - split.train_n - split.val_n),
        "sentinels": [
            {
                "name": r.name,
                "passed": r.passed,
                "status": r.status,
                "detail": r.detail,
                "evidence_sha256": r.evidence_sha256,
            }
            for r in results
        ],
        "all_passed": all(r.passed for r in results),
    }
    print(track_key, "all_passed =", out[track_key]["all_passed"], flush=True)

with open("/tmp/kq_sentinels.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print("SENTINELS_DONE")

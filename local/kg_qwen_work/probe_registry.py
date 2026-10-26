import json
import sys

sys.path.insert(0, "runtime/python-packages")
sys.path.insert(0, "src")

from epidemic_core.kg_qwen import TRACK_A_KEY, TRACK_B_KEY
from epidemic_core.kg_qwen import pipeline as kq
from epidemic_core.kg_pt import pipeline as pt_pipeline
from epidemic_core.kg_r3c.dashboard_app import write_conn

out = {}
with write_conn() as conn:
    for key in (TRACK_B_KEY, TRACK_A_KEY):
        track = kq._track(key)
        snap = conn.execute(
            "select snapshot_id::text, snapshot_sha256 from epidemic_kg.kg_pt_demo_snapshots "
            "where demo_key = %s order by created_at desc limit 1",
            (key,),
        ).fetchone()
        rows = pt_pipeline._load_frozen_rows(conn, snap["snapshot_id"])
        window = kq.track_eval_window_payload(key, [r.period_start for r in rows])
        snapshot = {"snapshot_sha256": snap["snapshot_sha256"], "rows": rows}
        split = kq._ensure_track_split(conn, track, snapshot)
        try:
            reg = kq._register_heldout_window(conn, track, snapshot, window, split)
            out[key] = {"outcome": "REGISTERED_OR_REPLAY", "status": reg["status"], "registry_id": reg["registry_id"]}
        except kq.HiddenEvalConsumed as exc:
            out[key] = {"outcome": "REFUSED", "error": str(exc)}
    raise SystemExit(json.dumps(out, ensure_ascii=False, default=str))

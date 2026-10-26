import json
import sys

sys.path.insert(0, "runtime/python-packages")
sys.path.insert(0, "src")

from epidemic_core.kg_qwen import TRACK_C_KEY
from epidemic_core.kg_qwen import pipeline as kq
from epidemic_core.kg_r3c.dashboard_app import write_conn

with write_conn() as conn:
    track = kq._track(TRACK_C_KEY)
    rows = kq._load_normalized_rows(conn, track["source_override"])
    tail = rows[-track["total_n"]:]
    nulls = sum(1 for r in tail if r.value is None)
    gaps = []
    from epidemic_core.forecast_model_kg.dual_loop import period_add

    for a, b in zip(tail, tail[1:]):
        expected = period_add(a.period_start, "week", 1)
        if expected != b.period_start:
            gaps.append((str(a.period_start), str(b.period_start)))
    vals = [r.value for r in tail if r.value is not None]
    hidden = tail[-track["hidden_n"]:]
    hvals = [r.value for r in hidden if r.value is not None]
    print(
        json.dumps(
            {
                "total_rows": len(rows),
                "tail_rows": len(tail),
                "tail_first": str(tail[0].period_start),
                "tail_last": str(tail[-1].period_start),
                "nulls_in_tail": nulls,
                "grid_gaps_in_tail": len(gaps),
                "gaps_sample": gaps[:5],
                "min_max_value": [min(vals), max(vals)],
                "hidden_first": str(hidden[0].period_start),
                "hidden_last": str(hidden[-1].period_start),
                "hidden_nulls": sum(1 for r in hidden if r.value is None),
                "hidden_mean": sum(hvals) / len(hvals) if hvals else None,
                "revision_max": max(r.revision for r in tail),
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    raise SystemExit(0)

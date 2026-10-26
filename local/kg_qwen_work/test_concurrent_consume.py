import json
import sys
import threading

sys.path.insert(0, "runtime/python-packages")
sys.path.insert(0, "src")

import psycopg

from epidemic_core.kg_qwen import pipeline as kq

DSN = "host=USER_HOME/projects/openclaw/runtime/postgres-run port=55432 dbname=epidemic_openclaw user=postgres"
RID = "00000000-0000-4000-8000-00000000c0de"

# setup: synthetic registry row (test scaffolding, deleted afterwards)
c = psycopg.connect(DSN, autocommit=True)
c.execute("delete from epidemic_kg.kg_qwen_heldout_registry where registry_id = %s", (RID,))
c.execute(
    """
    insert into epidemic_kg.kg_qwen_heldout_registry
        (registry_id, global_sha256, global_identity, track_key, identity, snapshot_sha256,
         eval_window, split_version, evaluator_version, status, registered_at, locked_at)
    values (%s,'synthetic','{}','SYNTH:concurrency:test:month','FRESH_HELDOUT_LOCKED','synthetic',
            '{}','synthetic','kg_qwen_heldout_evaluator.v2','LOCKED_UNOPENED',now(),now())
    """,
    (RID,),
)
c.close()

results: list[dict] = []
lock = threading.Lock()


def attempt(i: int) -> None:
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        out = kq._consume_heldout_global(conn, RID, f"00000000-0000-4000-8000-00000000000{i}", None, None)
        with lock:
            results.append({"worker": i, "outcome": "CONSUMED", "consumed_at": str(out["consumed_at"])})
    except kq.HiddenEvalConsumed as exc:
        with lock:
            results.append({"worker": i, "outcome": "REFUSED", "error": str(exc)})
    finally:
        conn.close()


threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()

consumed = [r for r in results if r["outcome"] == "CONSUMED"]
refused = [r for r in results if r["outcome"] == "REFUSED"]
state = psycopg.connect(DSN, autocommit=True)
final = state.execute(
    "select status, consumer_run_id::text, consumed_at::text from epidemic_kg.kg_qwen_heldout_registry where registry_id = %s",
    (RID,),
).fetchone()
state.execute("delete from epidemic_kg.kg_qwen_heldout_registry where registry_id = %s", (RID,))
state.close()

ok = len(consumed) == 1 and len(refused) == 7 and all("HELDOUT_ALREADY_CONSUMED" in r["error"] for r in refused) and final[0] == "CONSUMED"
payload = {
    "workers": 8,
    "consumed": len(consumed),
    "refused": len(refused),
    "final_status": final[0],
    "consumer_run_id": final[1],
    "results": sorted(results, key=lambda r: r["worker"]),
    "pass": ok,
}
with open("/tmp/kq_concurrency.json", "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=1)
print(json.dumps(payload, ensure_ascii=False, indent=1))

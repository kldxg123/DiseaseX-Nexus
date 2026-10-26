"""Execute the dual-dataset closed loop for Austria and Peru."""
import json
import sys

sys.path.insert(0, ".")
from db import connect  # noqa: E402

sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.forecast_model_kg.dual_loop import execute_shadow_run  # noqa: E402

RECS = {
    "A_austria": "c219de41-ea2e-58d9-86cd-339b03213401",
    "B_peru": "ca2f64b8-2926-5ca1-9d9e-4585980849c2",
}

out = {}
conn = connect()
try:
    for label, rid in RECS.items():
        result = execute_shadow_run(conn, rid)
        conn.commit()
        out[label] = result
        print(f"== {label}: status={result.get('status')} run_id={result.get('run_id')}")
        if result.get("champion"):
            c = result["champion"]
            print(f"   champion={c['champion_adapter_id']} source={c['champion_source']} kg_status={c['kg_model_status']}")
        if result.get("metrics"):
            for a, m in result["metrics"].items():
                print(f"   {a}: wape={m.get('wape')} mae={m.get('mae')} cov={m.get('interval_coverage')}")
        print(f"   origins={result.get('origin_count')} future={result.get('future_forecast_count')} feedback={result.get('feedback')}")
        print(f"   learning_cycle={result.get('learning_cycle')}")
        print(f"   not_eligible={result.get('not_eligible')}")
except Exception:
    conn.rollback()
    raise

with open("dual_loop_execution.json", "w") as f:
    json.dump(out, f, indent=2, default=str)
print("DONE")

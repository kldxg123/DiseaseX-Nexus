import json
from epidemic_core.kg_qwen.pipeline import autotuning_overview, TRACK_E_KEY, TRACK_F_KEY, TRACK_G_KEY
from epidemic_core.kg_r3c.dashboard_app import write_conn
with write_conn() as conn:
    conn.execute("set transaction read only")
    out=[]
    for key in (TRACK_E_KEY,TRACK_F_KEY,TRACK_G_KEY):
        o=autotuning_overview(conn,key) or {}
        e=o.get("evaluation") or {}; r=o.get("run") or {}; c=o.get("champion") or {}; p=e.get("paired") or {}; cert=e.get("certification") or {}
        out.append({"track_key":key,"run_id":r.get("run_id"),"target_status":r.get("target_status"),"champion_adapter_id":c.get("champion_adapter_id"),"wape":e.get("wape"),"agreement_pct":e.get("agreement_pct"),"coverage":e.get("coverage"),"best_baseline_id":e.get("best_baseline_id"),"best_baseline_wape":e.get("best_baseline_wape"),"paired":p,"certification":cert,"registry_id":e.get("registry_id"),"time_chain":e.get("time_chain")})
    print(json.dumps(out,ensure_ascii=False,sort_keys=True,default=str))
    conn.rollback()

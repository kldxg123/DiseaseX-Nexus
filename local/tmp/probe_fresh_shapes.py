from epidemic_core.kg_qwen.pipeline import autotuning_overview, TRACK_E_KEY, TRACK_F_KEY, TRACK_G_KEY
from epidemic_core.kg_r3c.dashboard_app import write_conn
with write_conn() as conn:
    conn.execute("set transaction read only")
    for key in (TRACK_E_KEY,TRACK_F_KEY,TRACK_G_KEY):
        o=autotuning_overview(conn,key) or {}
        print(key)
        for n in ("run","evaluation","champion","recommendation","heldout_registry","snapshot"):
            v=o.get(n)
            if isinstance(v,dict): print(n,sorted(v.keys()))
            elif isinstance(v,list): print(n,"list",len(v),sorted(v[0].keys()) if v and isinstance(v[0],dict) else [])
            else: print(n,type(v).__name__)
    conn.rollback()

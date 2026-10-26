from epidemic_core.kg_qwen.pipeline import autotuning_overview, TRACK_G_KEY
from epidemic_core.kg_r3c.dashboard_app import write_conn
with write_conn() as conn:
    conn.execute("set transaction read only")
    obj=autotuning_overview(conn,TRACK_G_KEY)
    print(sorted(obj.keys()) if obj else None)
    conn.rollback()

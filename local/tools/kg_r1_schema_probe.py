from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT = Path("USER_HOME/projects/openclaw")
REPORT = Path(os.environ.get("R1_REPORT_DIR", "USER_HOME/reports/kg_p1_r3r_ux2_r1_full_paper_reconciliation_backfill_20260804T035410Z"))

def find_pid():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except Exception:
            continue
        if "uvicorn" in cmd and "epidemic_core.dashboard.app:create_app" in cmd and cwd == str(PROJECT):
            return pid
    raise SystemExit("NO_DASHBOARD_PID")

def inherit(pid):
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            os.environ[k.decode()] = v.decode(errors="replace")

pid=find_pid()
inherit(pid)
for part in os.environ.get("PYTHONPATH", "").split(":"):
    if part and part not in sys.path:
        sys.path.insert(0, part)
sys.path.insert(0, str(PROJECT/"src"))
from sqlalchemy import create_engine, text
engine=create_engine(os.environ["DASHBOARD_DATABASE_URL"], connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=30000"}, hide_parameters=True)
with engine.connect() as c:
    cols=[dict(r) for r in c.execute(text("""
        select table_name,column_name,data_type,udt_name,is_nullable,column_default
        from information_schema.columns
        where table_schema='epidemic_kg'
        order by table_name,ordinal_position
    """)).mappings()]
    constraints=[dict(r) for r in c.execute(text("""
        select tc.table_name,tc.constraint_name,tc.constraint_type,kcu.column_name
        from information_schema.table_constraints tc
        left join information_schema.key_column_usage kcu
          on tc.constraint_schema=kcu.constraint_schema and tc.constraint_name=kcu.constraint_name
        where tc.table_schema='epidemic_kg'
        order by tc.table_name,tc.constraint_name,kcu.ordinal_position
    """)).mappings()]
out={"columns":cols,"constraints":constraints}
(REPORT/"KG_P1_R3R_UX2_R1_SCHEMA_PROBE.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"status":"SCHEMA_PROBE_OK","tables":len(set(x["table_name"] for x in cols))},ensure_ascii=False))

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    remote='/tmp/p1_14c_v2_preview_path/table_probe.py'
    code="""
import os,json
from sqlalchemy import create_engine,text
e=create_engine(os.environ['APP_DATABASE_URL'],hide_parameters=True)
with e.connect() as c:
 rows=c.execute(text(\"\"\"
 SELECT table_schema, table_name FROM information_schema.tables
 WHERE table_schema IN ('epidemic','epidemic_core','epidemic_p1_12','epidemic_discovery')
 AND (table_name ILIKE '%observation%' OR table_name ILIKE '%snapshot%' OR table_name ILIKE '%publication%' OR table_name ILIKE '%promotion%' OR table_name ILIKE '%decision%' OR table_name ILIKE '%schedule%')
 ORDER BY 1,2\"\"\")).mappings().all()
 print(json.dumps([dict(r) for r in rows]))
"""
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f: f.write(code)
    cmd=f"bash -lc {helper.q('cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; python3 '+helper.q(remote))}"
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,cmd,timeout=120)
    print(out)
finally:
    client.close()

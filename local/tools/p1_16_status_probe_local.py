from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path('tools').resolve()))
import a100_p1_11 as p111
client, _ = p111.p110r3.previous.p19.a100.connect()
try:
    data_root, project = p111.p110r3.previous.p19.a100.paths(client)
    py = f'{data_root}/conda/envs/epidemic-core/bin/python'
    code = """from pathlib import Path
for name in ['EXECUTION_STATE.json','model_metrics.json','predictions.csv']:
 p=Path('reports/ebola_historical_and_real_influenza_region_week_pipeline')/name
 print(name, p.exists(), p.stat().st_size if p.exists() else None, p.stat().st_mtime if p.exists() else None)
"""
    cmd = f"ps -eo pid,etime,stat,pcpu,pmem,cmd | grep p1_16_real_pipeline_remote.py | grep -v grep; cd {p111.q(project)}; {p111.q(py)} -c {p111.q(code)}"
    rc, out, err = p111.p110r3.previous.p19.a100.run(client, cmd, timeout=120)
    print(out)
    if err:
        print(err, file=sys.stderr)
finally:
    client.close()

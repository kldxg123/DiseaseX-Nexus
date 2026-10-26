from pathlib import Path
import json,importlib.util
root=Path.home()/'projects/openclaw'
out={'code_files':{},'instructions':[],'runtime_modules':{}}
for folder in ['src','tests','packages','apps','migrations']:
    paths=[]
    for p in (root/folder).rglob('*'):
        if p.is_file() and p.suffix in ('.py','.json') and not any(x in p.parts for x in ('__pycache__','node_modules','.git')):
            if any(x in p.name.lower() for x in ('auth','contract','identity','persist','workflow','gate','graph','forecast','recommend','migration')):
                paths.append(p.relative_to(root).as_posix())
    out['code_files'][folder]=paths[:100]
for p in (root/'experiments').glob('AGENTS.md'):
    out['instructions'].append(p.read_text())
import subprocess
py=Path.home()/'miniconda3/envs/biomni_e1/bin/python'
p=subprocess.run([str(py),'-c',"import importlib.util,json;print(json.dumps({x:importlib.util.find_spec(x) is not None for x in ['numpy','pytest','fastapi','alembic','sqlalchemy','httpx','torch','transformers']}))"],capture_output=True,text=True,timeout=20)
out['runtime_modules']=json.loads(p.stdout) if p.returncode==0 else {'probe_failed':True}
print(json.dumps(out))

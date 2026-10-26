import json,subprocess,urllib.request,os
from pathlib import Path
root=Path.home()/'projects/openclaw'
out={'instructions':[],'environments':[],'model_dirs':[],'model_services':[],'experiment_dirs':[],'data_dirs':[]}
for p in (Path.home()/'AGENTS.md',root/'AGENTS.md',root/'experiments/AGENTS.md'):
    if p.exists():out['instructions'].append(p.read_text())
for py in [root/'.venv/bin/python',*root.glob('experiments/*/venv*/bin/python')]:
    code="import json,importlib.util;print(json.dumps({m:importlib.util.find_spec(m)is not None for m in ['numpy','torch','transformers','pandas','llama_cpp','vllm']}))"
    try:
        r=subprocess.run([str(py),'-c',code],capture_output=True,text=True,timeout=20)
        out['environments'].append({'relative_python':str(py.relative_to(root)),'modules':json.loads(r.stdout)})
    except Exception:pass
for base in [Path.home()/'.cache/huggingface/hub',root/'models',root/'runtime',Path('/opt/models')]:
    if base.exists():
        out['model_dirs'] += [p.name for p in base.iterdir() if p.is_dir() and any(s in p.name.lower() for s in ['qwen','llama','model','llm','transform','conda'])]
for port in [8000,8001,8010,8080,11434]:
    for endpoint in ['/v1/models','/api/tags']:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{endpoint}',timeout=2) as r:data=json.load(r)
            out['model_services'].append({'port':port,'endpoint':endpoint,'models':[x.get('id',x.get('name')) for x in data.get('data',data.get('models',[]))]})
        except Exception:pass
out['experiment_dirs']=[p.name for p in (root/'experiments').iterdir() if p.is_dir()]
out['data_dirs']=[p.name for p in (root/'data').iterdir() if p.is_dir()]
print(json.dumps(out))

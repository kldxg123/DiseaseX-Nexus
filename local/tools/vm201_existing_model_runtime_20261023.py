import json,subprocess,os,importlib.util
from pathlib import Path
home=Path.home();root=home/'projects/openclaw';out={'environments':[],'model':{},'memory_available_gb':None}
mem=Path('/proc/meminfo').read_text()
for line in mem.splitlines():
 if line.startswith('MemAvailable:'):out['memory_available_gb']=round(int(line.split()[1])/1024**2,2)
for py in [home/'miniconda3/bin/python',*list((home/'miniconda3/envs').glob('*/bin/python'))]:
 if not py.exists():continue
 code="import json,importlib.metadata as m,importlib.util as u;print(json.dumps({k:(m.version(k) if u.find_spec(k) else None) for k in ['torch','transformers','accelerate','safetensors','numpy']}))"
 try:
  r=subprocess.run([str(py),'-c',code],capture_output=True,text=True,timeout=8)
  if r.returncode==0:out['environments'].append({'label':py.parent.parent.name,'versions':json.loads(r.stdout)})
 except Exception:pass
for current,dirs,files in os.walk(home):
 dirs[:]=[d for d in dirs if d not in ['.git','.ssh','node_modules','site-packages','.secrets','secrets']]
 if Path(current).name=='Qwen3-4B-Instruct-2507' and 'config.json' in files:
  model=Path(current);cfg=json.loads((model/'config.json').read_text());out['model']={'name':model.name,'architecture':cfg.get('architectures'),'model_type':cfg.get('model_type'),'dtype':cfg.get('torch_dtype',cfg.get('dtype')),'has_tokenizer':(model/'tokenizer.json').exists(),'weight_bytes':sum(p.stat().st_size for p in model.glob('*.safetensors'))}
  break
print(json.dumps(out))

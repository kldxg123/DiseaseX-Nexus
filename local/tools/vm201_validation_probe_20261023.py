import os,json,shutil,subprocess,importlib.util
from pathlib import Path
roots=[Path.home()/'projects/openclaw',Path.home()/'aibola']
result={'status':'connected','cpu_count':os.cpu_count(),'memory_gb':round(os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')/1024**3,1),
        'disk_free_gb':round(shutil.disk_usage(Path.home()).free/1024**3,1),
        'modules':{m:importlib.util.find_spec(m) is not None for m in ['numpy','torch','transformers','requests']},
        'commands':{m:bool(shutil.which(m)) for m in ['ollama','nvidia-smi','docker']},'projects':[]}
for i,root in enumerate(roots):
    if not root.exists():continue
    result['projects'].append({'root_id':i,'top_dirs':[p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')],
      'python_candidates':[str(p.relative_to(root)) for p in root.glob('**/bin/python') if len(p.relative_to(root).parts)<7][:25]})
try:
    r=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
    result['gpu']=r.stdout.strip() if r.returncode==0 else 'unavailable'
except Exception:result['gpu']='unavailable'
print(json.dumps(result))

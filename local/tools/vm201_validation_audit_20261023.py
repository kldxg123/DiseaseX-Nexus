import json,os,re,shutil
from pathlib import Path
root=Path.home()/'projects/openclaw'
out={'large_storage_exists':Path('USER_HOME/autodl-tmp').exists(),'large_storage_writable':os.access('USER_HOME/autodl-tmp',os.W_OK),'compilers':{x:bool(shutil.which(x)) for x in ['gcc','g++','cmake']},'model_files':[],'possible_new_window_files':[]}
for base in [root,Path.home()/'.cache',Path('/opt')]:
    for current,dirs,files in os.walk(base):
        dirs[:]=[d for d in dirs if d not in ['.git','node_modules','.secrets','secrets','postgres18x','site-packages']]
        for name in files:
            if name.endswith('.gguf'):out['model_files'].append({'basename':name,'bytes':(Path(current)/name).stat().st_size})
            low=name.lower()
            if any(k in low for k in ['singapore','mumps','campy','weeklyinfectious','bulletin']) and name.endswith(('.csv','.json','.pdf','.zip')):
                p=Path(current)/name
                if root in p.parents:out['possible_new_window_files'].append({'relative':str(p.relative_to(root)),'bytes':p.stat().st_size})
print(json.dumps(out))

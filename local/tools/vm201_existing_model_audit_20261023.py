"""Read-only deployment inventory; never emit process args, env, or private paths."""
from pathlib import Path
import os,json,subprocess,re,time
out={'model_files':[],'model_directories':[],'environments':[],'containers':[],'mount_filesystems':[],'scan_errors':0}
seen=set();start=time.monotonic()
skip={'.ssh','.git','node_modules','site-packages','__pycache__','.secrets','secrets','postgres-run','pgdata','postgres18x','proc','sys','dev','venv','venv_r4d','.venv'}
bases=[Path.home(),Path('/mnt'),Path('/data'),Path('/opt'),Path('/srv'),Path('/models'),Path('/media')]
for bi,base in enumerate(bases):
 if not base.exists():continue
 for current,dirs,files in os.walk(base,onerror=lambda e:None):
  if time.monotonic()-start>25:break
  dirs[:]=[d for d in dirs if d not in skip and not d.startswith('postgres')]
  p=Path(current)
  if p in seen:dirs[:]=[];continue
  seen.add(p)
  if any(k in p.name.lower() for k in ['qwen','ollama','llama','huggingface','modelscope']):
   out['model_directories'].append({'base_id':bi,'basename':p.name,'file_count':len(files)})
  for name in files:
   if name.endswith(('.gguf','.safetensors')) or (name.startswith('pytorch_model') and name.endswith('.bin')):
    try:out['model_files'].append({'base_id':bi,'basename':name,'parent_basename':p.name,'bytes':(p/name).stat().st_size})
    except OSError:out['scan_errors']+=1
  for d in dirs:
   if d in ['envs','bin']:
    py=p/d/'python'
    if py.is_file():out['environments'].append({'base_id':bi,'parent_basename':p.name})
try:
 r=subprocess.run(['docker','ps','-a','--format','{{.ID}}'],capture_output=True,text=True,timeout=8)
 out['docker_access']=r.returncode==0
 if r.returncode==0:
  for cid in r.stdout.split():
   info=json.loads(subprocess.run(['docker','inspect',cid],capture_output=True,text=True,timeout=5).stdout)[0]
   image=info.get('Config',{}).get('Image','').rsplit('/',1)[-1]
   out['containers'].append({'image_basename':image,'running':info.get('State',{}).get('Running'),
     'mount_destination_basenames':[Path(m.get('Destination','')).name for m in info.get('Mounts',[])],
     'model_related':any(k in json.dumps(info.get('Config',{})).lower() for k in ['qwen','vllm','ollama','llama'])})
except Exception as e:out['docker_error_type']=type(e).__name__
try:
 r=subprocess.run(['findmnt','-rn','-o','FSTYPE,TARGET'],capture_output=True,text=True,timeout=5)
 for line in r.stdout.splitlines():
  typ,_,target=line.partition(' ')
  if typ in ['ext4','xfs','btrfs','nfs','nfs4','cifs']:
   out['mount_filesystems'].append({'type':typ,'target_basename':Path(target).name or 'root'})
except Exception:pass
out['scan_seconds']=time.monotonic()-start
print(json.dumps(out))

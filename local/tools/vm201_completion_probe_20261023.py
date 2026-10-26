"""Read-only environment and instruction inventory; no service credentials."""
from pathlib import Path
import json, os, shutil, subprocess

root=Path.home()/'projects/openclaw'
out={'project_exists':root.is_dir(),'instructions':[], 'environment':{},'project_subdirs':[]}
for path in (Path.home()/'AGENTS.md',Path.home()/'projects/AGENTS.md',root/'AGENTS.md'):
    if path.is_file():
        out['instructions'].append({'scope':'project' if path.parent==root else 'ancestor','text':path.read_text()})
out['project_subdirs']=[p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')]
for command in ('docker','podman','dot','git'):
    out['environment'][command+'_available']=bool(shutil.which(command))
if shutil.which('docker'):
    for name,args in [('docker_daemon',['docker','info','--format','{{.ServerVersion}}']),('compose',['docker','compose','version','--short'])]:
        p=subprocess.run(args,capture_output=True,text=True,timeout=15)
        out['environment'][name]={'returncode':p.returncode,'version':p.stdout.strip() if p.returncode==0 else None}
out['environment']['disk_free_gb']=round(shutil.disk_usage(root).free/1024**3,1)
out['candidate_roots']={n:(root/n).is_dir() for n in ('packages','src','tests','apps','experiments')}
print(json.dumps(out))

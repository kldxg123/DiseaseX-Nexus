from pathlib import Path
from datetime import datetime,timezone
import urllib.request,json,hashlib
ROOT=Path(__file__).resolve().parents[1]/'.codex_tmp/frozen_validation_model'
ROOT.mkdir(parents=True,exist_ok=True)
repo='Qwen/Qwen2.5-1.5B-Instruct-GGUF';name='qwen2.5-1.5b-instruct-q4_k_m.gguf'
with urllib.request.urlopen('https://huggingface.co/api/models/'+repo+'?blobs=true',timeout=30) as r:meta=json.load(r)
entry=next(s for s in meta['siblings'] if s['rfilename']==name);expected=entry['lfs']['sha256'];revision=meta['sha']
source={'repo':repo,'revision':revision,'filename':name,'expected_sha256':expected,'licence':'Apache-2.0','metadata_at':datetime.now(timezone.utc).isoformat(),'transport':'official host download on local workstation, pinned bytes transferred over verified SSH'}
(ROOT/'model_source.json').write_text(json.dumps(source,indent=2))
target=ROOT/'model.gguf'
if not target.exists():
    with urllib.request.urlopen(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',timeout=90) as r,target.with_suffix('.part').open('wb') as f:
        while True:
            chunk=r.read(8*1024*1024)
            if not chunk:break
            f.write(chunk)
    target.with_suffix('.part').rename(target)
assert hashlib.sha256(target.read_bytes()).hexdigest()==expected
print(json.dumps({'model_downloaded':True,'bytes':target.stat().st_size,'sha256_verified':True}))

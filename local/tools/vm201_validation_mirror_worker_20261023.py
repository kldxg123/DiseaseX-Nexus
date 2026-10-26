"""Transport a publicly licensed model; verify against official repository SHA."""
from pathlib import Path
import urllib.request,json,hashlib,subprocess,sys,time
root=Path(__file__).resolve().parent
source=json.loads((root/'model_source.json').read_text())
url='https://hf-mirror.com/'+source['repo']+'/resolve/'+source['revision']+'/'+source['filename']
def state(**data):(root/'model_transfer_status.json').write_text(json.dumps(data))
try:
    state(stage='public_mirror_download',bytes=0)
    target=root/'model.mirror.part';size=0;last=time.monotonic()
    with urllib.request.urlopen(url,timeout=60) as response,target.open('wb') as f:
        while True:
            chunk=response.read(4*1024*1024)
            if not chunk:break
            f.write(chunk);size+=len(chunk)
            if time.monotonic()-last>5:state(stage='public_mirror_download',bytes=size);last=time.monotonic()
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    if digest!=source['expected_sha256']:raise RuntimeError('Official checksum mismatch')
    existing=root/'model.gguf'
    if existing.exists():existing.rename(root/'model.incomplete_upload')
    target.rename(existing)
    state(stage='verified',bytes=size,official_sha256_match=True)
    subprocess.run([sys.executable,str(root/'run_frozen_llm_validation_20261023.py')],check=True)
except Exception as e:
    state(stage='failed',error_type=type(e).__name__)

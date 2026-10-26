"""Isolated CPU-only environment; no production service changes."""
from pathlib import Path
import subprocess,json,sys,os,time,hashlib,urllib.request

def main():
    root=Path(__file__).resolve().parent
    state={'stage':'environment','started_at':time.time(),'gpu_layers':0}
    def save():(root/'bootstrap_status.json').write_text(json.dumps(state,indent=2))
    save()
    py=root/'venv/bin/python'
    if not py.exists() or subprocess.run([str(py),'-m','pip','--version'],capture_output=True).returncode:
        state['stage']='virtualenv_bootstrap';save()
        url='https://bootstrap.pypa.io/virtualenv.pyz'
        with urllib.request.urlopen(url,timeout=60) as response:raw=response.read()
        (root/'virtualenv.pyz').write_bytes(raw)
        state['virtualenv_zipapp_sha256']=hashlib.sha256(raw).hexdigest();save()
        subprocess.run([sys.executable,str(root/'virtualenv.pyz'),str(root/'venv')],check=True,capture_output=True)
    with (root/'install.log').open('w') as log:
        r=subprocess.run([str(py),'-m','pip','install','--only-binary=:all:','numpy','pypdf','requests','llama-cpp-python',
          '--extra-index-url','https://abetlen.github.io/llama-cpp-python/whl/cpu'],stdout=log,stderr=log,timeout=900)
    state.update(install_exit=r.returncode,stage='ready' if r.returncode==0 else 'install_failed');save()

if __name__=='__main__':
    try:main()
    except Exception as e:
        (Path(__file__).resolve().parent/'bootstrap_failure.json').write_text(json.dumps({'error_type':type(e).__name__}))

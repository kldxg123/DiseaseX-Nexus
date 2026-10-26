from __future__ import annotations
import json, shutil, subprocess
bins=['chromium','chromium-browser','google-chrome','google-chrome-stable','chrome']
found={b: shutil.which(b) for b in bins}
versions={}
for b,p in found.items():
 if p:
  try:
   r=subprocess.run([p,'--version'],text=True,capture_output=True,timeout=10)
   versions[b]={'returncode':r.returncode,'stdout':r.stdout.strip(),'stderr':r.stderr.strip()}
  except Exception as e: versions[b]={'error':type(e).__name__}
print(json.dumps({'found':found,'versions':versions},ensure_ascii=False,indent=2))

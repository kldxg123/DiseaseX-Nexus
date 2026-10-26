import json,urllib.request,urllib.error,ssl,socket
from pathlib import Path
p=Path.home()/'projects/openclaw/experiments/frozen_llm_validation_20261023'
error=(p/'failure.log').read_text() if (p/'failure.log').exists() else ''
out={'prior_error_flags':{k:k in error.lower() for k in ['ssl','certificate','timed out','name resolution','network','reset','refused']},'mirror':{}}
url='https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf'
try:
 req=urllib.request.Request(url,method='HEAD')
 with urllib.request.urlopen(req,timeout=15) as r:out['mirror']={'status':r.status,'content_length':r.headers.get('Content-Length')}
except Exception as e:out['mirror']={'error_type':type(e).__name__}
print(json.dumps(out))

import urllib.request,json,hashlib,time
urls=[
'https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf?download=true',
'https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf?download=true']
out=[]
for i,url in enumerate(urls):
 t=time.monotonic()
 try:
  req=urllib.request.Request(url,headers={'Range':'bytes=0-1048575'})
  with urllib.request.urlopen(req,timeout=12) as r:
   data=r.read(1048576);out.append({'source_index':i,'http_status':r.status,'bytes':len(data),'sha256_prefix':hashlib.sha256(data).hexdigest(),'seconds':time.monotonic()-t})
 except Exception as e:out.append({'source_index':i,'error_type':type(e).__name__,'seconds':time.monotonic()-t})
print(json.dumps(out))

from test_utils import chat,post
import json,urllib.request
prompts=["用一句中文回答：你好。","Answer briefly in English: what is water?","中英文各用一个词表示健康 / health.","将疾病名称 Ebola virus disease 标准化为常用英文名。","Return only strict JSON with key ok and boolean true."]
r=[]
for x in prompts:
 z=chat(x);m=z["response"]["choices"][0]["message"];r.append({"ok":z["status"]==200 and bool(m.get("content")),"elapsed":z["elapsed"],"model":z["response"].get("model"),"finish_reason":z["response"]["choices"][0].get("finish_reason"),"usage":z["response"].get("usage",{})})
# streaming
p={"model":"qwen3-8b-awq-local","messages":[{"role":"user","content":"Count from one to three briefly."}],"max_tokens":32,"stream":True,"temperature":0}
req=urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions",data=json.dumps(p).encode(),headers={"Content-Type":"application/json"});chunks=0;content="";done=False
with urllib.request.urlopen(req,timeout=180) as f:
 for raw in f:
  line=raw.decode().strip();
  if not line.startswith("data: "):continue
  d=line[6:];
  if d=="[DONE]":done=True;continue
  chunks+=1;j=json.loads(d);content+=(j["choices"][0].get("delta",{}).get("content") or "")
# thinking off/on
off=chat("Answer only: 2",chat_template_kwargs={"enable_thinking":False});om=off["response"]["choices"][0]["message"]
on=chat("Think briefly, then answer: 1+1?",chat_template_kwargs={"enable_thinking":True},max_tokens=256);tm=on["response"]["choices"][0]["message"]
out={"normal":r,"normal_pass":all(x["ok"] and x["model"]=="qwen3-8b-awq-local" for x in r),"stream":{"pass":chunks>1 and done and bool(content),"chunks":chunks},"thinking_off":{"pass":bool(om.get("content")) and "<think>" not in om.get("content","")},"thinking_on":{"pass":bool(tm.get("content")) and bool(tm.get("reasoning_content") or tm.get("reasoning")),"reasoning_field":next((k for k in ("reasoning_content","reasoning") if tm.get(k)),None)}}
print(json.dumps(out))

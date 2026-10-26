from __future__ import annotations
import json,time,urllib.request,urllib.error
URL="http://127.0.0.1:8000/v1/chat/completions"
def post(payload,timeout=180):
 b=json.dumps(payload).encode(); r=urllib.request.urlopen(urllib.request.Request(URL,data=b,headers={"Content-Type":"application/json"}),timeout=timeout); return r.status,json.load(r)
def chat(prompt,**extra):
 p={"model":"qwen3-8b-awq-local","messages":[{"role":"user","content":prompt}],"max_tokens":128,"temperature":0};p.update(extra);t=time.monotonic();s,d=post(p);return {"status":s,"elapsed":time.monotonic()-t,"response":d}
def tool():
 return {"type":"function","function":{"name":"normalize_epidemic_query","description":"Normalize a hypothetical query without executing it.","parameters":{"type":"object","properties":{"country":{"type":"string"},"disease":{"type":"string"},"start_date":{"type":"string","pattern":"^\\d{4}-\\d{2}-\\d{2}$"},"end_date":{"type":"string","pattern":"^\\d{4}-\\d{2}-\\d{2}$"},"forecast_horizon_days":{"type":"integer","minimum":1,"maximum":365}},"required":["country","disease","start_date","end_date","forecast_horizon_days"],"additionalProperties":False}}}
def valid_call(msg):
 tc=msg.get("tool_calls") or [];
 if len(tc)!=1 or tc[0].get("function",{}).get("name")!="normalize_epidemic_query":return False
 try:a=json.loads(tc[0]["function"]["arguments"])
 except Exception:return False
 return set(a)=={"country","disease","start_date","end_date","forecast_horizon_days"} and isinstance(a["country"],str) and isinstance(a["disease"],str) and isinstance(a["forecast_horizon_days"],int) and 1<=a["forecast_horizon_days"]<=365 and __import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}",a["start_date"]) is not None and __import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}",a["end_date"]) is not None

from test_utils import chat,post,tool,valid_call
import json
T=tool(); base=["Normalize an Ebola query for Uganda from 2026-01-01 to 2026-02-01 with a 14 day horizon.","刚果民主共和国，猴痘，2026-03-01至2026-03-31，预测7天。","Prepare a cholera query for Kenya, 2026-04-01 through 2026-04-30, horizon 30 days."]
def one(prompt,choice="auto",thinking=False):
 z=chat(prompt,tools=[T],tool_choice=choice,chat_template_kwargs={"enable_thinking":thinking},max_tokens=512 if thinking else 128);m=z["response"]["choices"][0]["message"];return z,m,valid_call(m)
req=[one(x,"required")[2] for x in base]
auto=[one(x)[2] for x in base+["请规范化尼日利亚拉沙热查询：2026-05-01到2026-05-20，未来10天。","Normalize measles in Ghana, 2026-06-01 to 2026-06-15, horizon 21."]]
no=[]
for x in ["What is the capital of France?","用一句话解释水是什么。","Say hello in English."]:
 z=chat(x,tools=[T],tool_choice="auto");m=z["response"]["choices"][0]["message"];no.append(bool(m.get("content")) and not m.get("tool_calls"))
z,m,ok=one(base[0],"required");tc=m.get("tool_calls",[])[0];msgs=[{"role":"user","content":base[0]},{"role":"assistant","content":m.get("content"),"tool_calls":m["tool_calls"]},{"role":"tool","tool_call_id":tc["id"],"name":"normalize_epidemic_query","content":json.dumps({"normalized":True,"records":0})}];s,d=post({"model":"qwen3-8b-awq-local","messages":msgs,"tools":[T],"max_tokens":96,"temperature":0});feedback=bool(d["choices"][0]["message"].get("content")) and not d["choices"][0]["message"].get("tool_calls")
z,m,thinkok=one(base[1],"required",True);thinking_tool=thinkok and not (m.get("content") and "<think>" in m.get("content",""))
out={"required":{"passed":sum(req),"total":3,"details":req},"auto":{"passed":sum(auto),"total":5,"details":auto},"no_tool":{"passed":sum(no),"total":3,"details":no},"tool_result_feedback":feedback,"thinking_tool":thinking_tool};print(json.dumps(out))

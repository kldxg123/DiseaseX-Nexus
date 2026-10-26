from concurrent.futures import ThreadPoolExecutor
from test_utils import chat,tool,valid_call
import json,time,subprocess
def a():return chat("Answer briefly: what is public health?")
def b():return chat("Normalize cholera in Kenya from 2026-01-01 to 2026-01-10, horizon 7.",tools=[tool()],tool_choice="required")
t=time.monotonic()
with ThreadPoolExecutor(max_workers=2) as ex:x,y=ex.submit(a),ex.submit(b);x=x.result();y=y.result()
mem=subprocess.check_output(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"],text=True).strip();out={"pass":bool(x["response"]["choices"][0]["message"].get("content")) and valid_call(y["response"]["choices"][0]["message"]),"ordinary_latency":x["elapsed"],"tool_latency":y["elapsed"],"wall_time":time.monotonic()-t,"memory_used_mib":int(mem)};print(json.dumps(out))

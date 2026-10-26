import json,subprocess,time
q=["nvidia-smi","--query-gpu=name,memory.total,memory.used,memory.free,driver_version","--format=csv,noheader,nounits"];print(json.dumps({"timestamp":time.time(),"gpu":subprocess.check_output(q,text=True).strip()}))

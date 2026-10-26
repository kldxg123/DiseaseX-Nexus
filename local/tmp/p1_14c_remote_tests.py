from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    data_root,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    py=f'{data_root}/conda/envs/epidemic-core/bin/python'
    cmds=[
      ('py_compile', f'{helper.q(py)} -m py_compile src/epidemic_core/ebola/fetcher.py src/epidemic_core/p1_12/discovery.py migrations/versions/0027_p1_14c_v2_preview_path_grants.py tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py'),
      ('pytest_p1_14c', f'{helper.q(py)} -m pytest tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py -q'),
      ('pytest_existing_gate', f'{helper.q(py)} -m pytest tests/unit/test_p1_12_review_gate.py tests/unit/test_p1_12_trusted_and_permissions.py -q'),
    ]
    results=[]
    for name,cmd in cmds:
        full=f"bash -lc {helper.q('set -euo pipefail; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; cd '+helper.q(project)+'; export PYTHONPATH='+helper.q(project+'/src')+'; '+cmd)}"
        rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client, full, timeout=600)
        results.append({'name':name,'rc':rc,'stdout':out[-4000:],'stderr':err[-4000:]})
        print(json.dumps(results[-1], ensure_ascii=False))
        if rc!=0: break
finally:
    client.close()

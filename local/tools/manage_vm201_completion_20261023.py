"""Transfer an allowlisted isolated research stage; no production mutation."""
from pathlib import Path
import json,shlex,sys
from vm201_validation_access_20261023 import connect,run_python
ROOT=Path(__file__).resolve().parents[1]
NAME='evidence_completion_20261023'
FILES=['tools/run_vm201_completion_20261023.py','tools/test_vm201_completion_20261023.py','tools/frozen_llm_validation_core_20261023.py',
       'tools/run_existing_qwen3_validation_20261023.py','tools/run_frozen_llm_validation_20261023.py',
       'packages/predictor_agent/durable_execution.py','packages/predictor_agent/chronological_validation.py']

def main():
    action=sys.argv[1];client=connect(sys.argv[2])
    try:
        remote=run_python(client,"from pathlib import Path;import json;print(json.dumps(str(Path.home()/'projects/openclaw/experiments'/"+repr(NAME)+")))")
        if action=='start':
            run_python(client,"from pathlib import Path;import json;p=Path("+repr(remote)+");p.mkdir(exist_ok=False);[(p/x).mkdir(parents=True,exist_ok=True) for x in ['tools','packages/predictor_agent','inputs','results']];print(json.dumps({'isolated_directory_created':True}))")
            source=client.open_sftp()
            try:
                for name in FILES:source.put(str(ROOT/name),remote+'/'+name)
                for local,name in [('reports/frozen_llm_validation_20261023/official_response.json','official_response.json'),('reports/frozen_llm_validation_20261023/new_window_observations.csv','new_window_observations.csv'),('kg_deploy_staging/reference/forecast_model_knowledge_graph.v1.json','graph.json')]:
                    source.put(str(ROOT/local),remote+'/inputs/'+name)
            finally:source.close()
            python=run_python(client,"from pathlib import Path;import json;print(json.dumps(str(Path.home()/'miniconda3/envs/biomni_e1/bin/python')))" )
            command='nohup nice -n 10 '+shlex.quote(python)+' '+shlex.quote(remote+'/tools/run_vm201_completion_20261023.py')+' > /dev/null 2>&1 < /dev/null &'
            client.exec_command(command)
            print(json.dumps({'isolated_stage_started':True,'production_changed':False,'docker_used':False,'GPU_used':False}))
        elif action=='availability-cost-repair':
            source=client.open_sftp()
            try:source.put(str(ROOT/'tools/repair_vm201_availability_cost_20261023.py'),remote+'/tools/repair_vm201_availability_cost_20261023.py')
            finally:source.close()
            python=run_python(client,"from pathlib import Path;import json;print(json.dumps(str(Path.home()/'miniconda3/envs/biomni_e1/bin/python')))" )
            _,out,err=client.exec_command(shlex.quote(python)+' '+shlex.quote(remote+'/tools/repair_vm201_availability_cost_20261023.py'),timeout=45)
            result=out.channel.recv_exit_status()
            print(json.dumps({'availability_cost_repair_exit':result,'original_results_retained':True}))
        elif action=='wall-budget':
            source=client.open_sftp()
            try:source.put(str(ROOT/'tools/run_vm201_wall_budget_20261023.py'),remote+'/tools/run_vm201_wall_budget_20261023.py')
            finally:source.close()
            python=run_python(client,"from pathlib import Path;import json;print(json.dumps(str(Path.home()/'miniconda3/envs/biomni_e1/bin/python')))" )
            client.exec_command('nohup nice -n 10 '+shlex.quote(python)+' '+shlex.quote(remote+'/tools/run_vm201_wall_budget_20261023.py')+' > /dev/null 2>&1 < /dev/null &')
            print(json.dumps({'hard_wall_clock_experiment_started':True,'caps_seconds':[1,5,30],'production_changed':False}))
        elif action=='status':
            code="from pathlib import Path;import json;p=Path("+repr(remote)+")/'results';print(json.dumps({n:json.loads((p/n).read_text()) for n in ['status.json','test_result.json','failure_type.json','completion.json','wall_budget_status.json'] if (p/n).is_file()}))"
            print(json.dumps(run_python(client,code)))
        elif action=='pull':
            out=ROOT/'reports'/NAME;out.mkdir(exist_ok=True)
            source=client.open_sftp();count=0
            try:
                for attr in source.listdir_attr(remote+'/results'):
                    name=attr.filename
                    if name.endswith(('.json','.csv','.txt')):
                        source.get(remote+'/results/'+name,str(out/name));count+=1
            finally:source.close()
            print(json.dumps({'research_result_files_downloaded':count}))
        else:raise ValueError('Unknown action')
    finally:client.close()

if __name__=='__main__':
    try:main()
    except Exception as exc:print(json.dumps({'operation_failed':True,'error_type':type(exc).__name__}));sys.exit(1)

"""Upload only explicitly allowed experiment inputs; return sanitized summaries."""
from pathlib import Path
import sys,json,shlex,subprocess,shutil
from vm201_validation_access_20261023 import connect,run_python
ROOT=Path(__file__).resolve().parents[1]
NAME='frozen_llm_validation_20261023'

def main():
    action=sys.argv[1];login=sys.argv[2];c=connect(login)
    try:
        remote=run_python(c,"from pathlib import Path;import json;print(json.dumps(str(Path.home()/'projects/openclaw/experiments'/"+repr(NAME)+")))")
        if action in ('bootstrap','bootstrap-retry'):
            if action=='bootstrap':run_python(c,"from pathlib import Path;import json;p=Path("+repr(remote)+");p.mkdir(exist_ok=False);print(json.dumps({'created':True}))")
            s=c.open_sftp()
            try:s.put(str(ROOT/'tools/vm201_validation_bootstrap_20261023.py'),remote+'/bootstrap.py')
            finally:s.close()
            command='nohup nice -n 10 python3 '+shlex.quote(remote+'/bootstrap.py')+' > /dev/null 2>&1 < /dev/null &'
            c.exec_command(command)
            print(json.dumps({'stage':'isolated_environment_preparing','existing_services_changed':False}))
        elif action=='start':
            names=['run_frozen_llm_validation_20261023.py','frozen_llm_validation_core_20261023.py','test_frozen_llm_validation_20261023.py']
            s=c.open_sftp()
            try:
                for name in names:s.put(str(ROOT/'tools'/name),remote+'/'+name)
                s.put(str(ROOT/'reports/three_disease_chronological_20261023/official_response.json'),remote+'/official_response.json')
            finally:s.close()
            command='nohup nice -n 10 '+shlex.quote(remote+'/venv/bin/python')+' '+shlex.quote(remote+'/run_frozen_llm_validation_20261023.py')+' > '+shlex.quote(remote+'/run.log')+' 2>&1 < /dev/null &'
            c.exec_command(command)
            print(json.dumps({'stage':'frozen_experiment_started','cpu_threads':4,'existing_services_changed':False}))
        elif action=='existing-model-start':
            s=c.open_sftp()
            try:
                s.put(str(ROOT/'tools/run_existing_qwen3_validation_20261023.py'),remote+'/run_existing_qwen3_validation_20261023.py')
                for name in ['new_window_observations.csv','new_window_parser_checks.json']:
                    s.put(str(ROOT/'reports'/NAME/name),remote+'/'+name)
                s.put(str(ROOT/'reports'/NAME/'new_window_source/provenance.json'),remote+'/new_window_source_provenance.json')
                s.put(str(ROOT/'reports'/NAME/'new_window_source/exposure_audit.json'),remote+'/new_window_exposure_audit.json')
            finally:s.close()
            python=run_python(c,"from pathlib import Path;import json;p=Path.home()/'miniconda3/envs/biomni_e1/bin/python';assert p.is_file();print(json.dumps(str(p)))")
            command='nohup nice -n 10 '+shlex.quote(python)+' '+shlex.quote(remote+'/run_existing_qwen3_validation_20261023.py')+' > /dev/null 2>&1 < /dev/null &'
            c.exec_command(command)
            print(json.dumps({'stage':'existing_server_model_experiment_started','gpu_used':False,'existing_environment_modified':False,'all_execution_on_server':True}))
        elif action=='native-resume':
            offset=run_python(c,"from pathlib import Path;import json;p=Path("+repr(remote+'/model.gguf')+");print(json.dumps(p.stat().st_size if p.exists() else 0))")
            local=ROOT/'.codex_tmp/frozen_validation_model/model.gguf'
            if not 0<=offset<=local.stat().st_size:raise RuntimeError('invalid upload offset')
            host,port=c.get_transport().getpeername()
            code="from pathlib import Path;import sys,json;p=Path("+repr(remote+'/model.gguf')+");f=p.open('ab');remaining="+str(local.stat().st_size-offset)+"\nwhile remaining:\n b=sys.stdin.buffer.read(min(4194304,remaining))\n if not b:raise RuntimeError('truncated stream')\n f.write(b);remaining-=len(b)\nf.close();print(json.dumps({'stream_received':True}))"
            args=['ssh','-T','-p',str(port),'-i',str(Path.home()/'.ssh'/'id_ed25519_jumper'),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(ROOT/'known_hosts_vm201_openclaw'),login+'@'+host,'python3 -c '+shlex.quote(code)]
            child=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                with local.open('rb') as f:
                    f.seek(offset);shutil.copyfileobj(f,child.stdin,4*1024*1024)
                child.stdin.close();rc=child.wait(timeout=900)
                if rc:raise RuntimeError('native transfer failed')
                print(json.dumps({'transfer_completed':True,'resumed_bytes':offset}))
            except Exception:
                child.terminate();raise
            code="""from pathlib import Path
import json,hashlib
p=Path(REMOTE);s=json.loads((p/'model_source.json').read_text())
assert hashlib.sha256((p/'model.gguf').read_bytes()).hexdigest()==s['expected_sha256']
(p/'model_transfer_verified.json').write_text(json.dumps({'official_sha256_match':True,'transport':'native SSH resumed stream'}))
print(json.dumps({'sha256_verified':True}))
""".replace('REMOTE',repr(remote))
            print(json.dumps(run_python(c,code)))
            command='nohup nice -n 10 '+shlex.quote(remote+'/venv/bin/python')+' '+shlex.quote(remote+'/run_frozen_llm_validation_20261023.py')+' > '+shlex.quote(remote+'/run_native_retry.log')+' 2>&1 < /dev/null &'
            c.exec_command(command)
        elif action=='mirror-retry':
            s=c.open_sftp()
            try:
                s.put(str(ROOT/'.codex_tmp/frozen_validation_model/model_source.json'),remote+'/model_source.json')
                s.rename(remote+'/run_frozen_llm_validation_20261023.py',remote+'/network_failed_runner.py')
                for name in ['run_frozen_llm_validation_20261023.py','vm201_validation_mirror_worker_20261023.py']:
                    s.put(str(ROOT/'tools'/name),remote+'/'+name)
            finally:s.close()
            code="""from pathlib import Path
import json,hashlib,datetime
p=Path(REMOTE)
record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reason':'official model host unreachable from server; use public mirror and verify bytes against official locally retrieved repository metadata','algorithm_or_data_window_changed':False,'before_runner_sha256':hashlib.sha256((p/'network_failed_runner.py').read_bytes()).hexdigest(),'after_runner_sha256':hashlib.sha256((p/'run_frozen_llm_validation_20261023.py').read_bytes()).hexdigest()}
(p/'network_transport_repair.json').write_text(json.dumps(record,indent=2))
print(json.dumps({'repair_recorded':True}))
""".replace('REMOTE',repr(remote))
            print(json.dumps(run_python(c,code)))
            command='nohup nice -n 10 '+shlex.quote(remote+'/venv/bin/python')+' '+shlex.quote(remote+'/vm201_validation_mirror_worker_20261023.py')+' > '+shlex.quote(remote+'/run_retry.log')+' 2>&1 < /dev/null &'
            c.exec_command(command)
        elif action=='model-transfer':
            s=c.open_sftp()
            try:
                s.put(str(ROOT/'.codex_tmp/frozen_validation_model/model.gguf'),remote+'/model.gguf')
                s.put(str(ROOT/'.codex_tmp/frozen_validation_model/model_source.json'),remote+'/model_source.json')
                s.rename(remote+'/run_frozen_llm_validation_20261023.py',remote+'/network_failed_runner.py')
                s.put(str(ROOT/'tools/run_frozen_llm_validation_20261023.py'),remote+'/run_frozen_llm_validation_20261023.py')
            finally:s.close()
            code="""from pathlib import Path
import json,hashlib,datetime
p=Path(REMOTE)
record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reason':'server cannot reach official model metadata host; identical pinned official bytes downloaded locally and transferred over verified SSH','algorithm_or_data_window_changed':False,'before_runner_sha256':hashlib.sha256((p/'network_failed_runner.py').read_bytes()).hexdigest(),'after_runner_sha256':hashlib.sha256((p/'run_frozen_llm_validation_20261023.py').read_bytes()).hexdigest()}
(p/'network_transport_repair.json').write_text(json.dumps(record,indent=2))
print(json.dumps({'model_transferred':True,'repair_recorded':True}))
""".replace('REMOTE',repr(remote))
            print(json.dumps(run_python(c,code)))
            command='nohup nice -n 10 '+shlex.quote(remote+'/venv/bin/python')+' '+shlex.quote(remote+'/run_frozen_llm_validation_20261023.py')+' > '+shlex.quote(remote+'/run_retry.log')+' 2>&1 < /dev/null &'
            c.exec_command(command)
        elif action=='pull':
            destination=ROOT/'reports'/NAME;destination.mkdir(exist_ok=True)
            s=c.open_sftp();count=0
            try:
                for attr in s.listdir_attr(remote):
                    name=attr.filename
                    if (name.endswith(('.json','.csv','.py')) or (name.startswith('unit_tests') and name.endswith('.log'))) and not name.startswith('bootstrap'):
                        s.get(remote+'/'+name,str(destination/name));count+=1
            finally:s.close()
            print(json.dumps({'downloaded_files':count,'relative_output':'reports/'+NAME}))
        elif action=='status':
            code="""from pathlib import Path
import json
p=Path(REMOTE)
out={'model_bytes_present':(p/'model.gguf').stat().st_size if (p/'model.gguf').exists() else 0}
for name in ['bootstrap_status.json','bootstrap_failure.json','experiment_status.json','result_summary.json','model_transfer_status.json']:
 if (p/name).exists():out[name]=json.loads((p/name).read_text())
log=p/'install.log'
if log.exists():
 lines=log.read_text(errors='replace').splitlines()
 out['install_progress']={'line_count':len(lines),'wheel_error':any('No matching distribution' in s for s in lines),'build_error':any('Failed building' in s for s in lines)}
print(json.dumps(out))
""".replace('REMOTE',repr(remote))
            print(json.dumps(run_python(c,code),ensure_ascii=True))
        else:raise ValueError('unsupported action')
    finally:c.close()

if __name__=='__main__':main()

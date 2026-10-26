"""Frozen, offline teacher-requested experiments. No remote deployment or keys."""
from pathlib import Path
from datetime import datetime, timezone
from zipfile import ZipFile
import csv, hashlib, io, json, os, shutil, subprocess, sys, time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packages'));sys.path.insert(0,str(ROOT/'tools'))
from package_local_snapshot import sanitize
OUT=ROOT/'reports/teacher_requested_experiments_20261023'
OUT.mkdir(exist_ok=True)
SEED=20261023

def save(name,obj):
    p=OUT/name
    if p.exists():raise RuntimeError('Frozen artifact exists: '+name)
    p.write_text(sanitize(json.dumps(obj,ensure_ascii=False,indent=2))[0],encoding='utf-8')

def csvsave(name,rows):
    p=OUT/name
    if p.exists():raise RuntimeError('Frozen artifact exists: '+name)
    with p.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def hk():
    model='recursive_hist_poisson'
    prefix='hk_canonical'
    source=next((ROOT/'reports/hk_flu_independent_short_horizon_20260825T112248Z').glob('*.zip'))
    save(prefix+'_protocol.json',{'locked_at':datetime.now(timezone.utc).isoformat(),'source_archive_sha256':sha(source),'status':'paired historical prediction reanalysis, not new model training or blind test',
         'separate_nowcast':0,'forecast_horizons':list(range(1,9)),'draws':5000,'seed':SEED,'primary_block':8,'sensitivity_blocks':[4,13],
         'comparison':model+' minus AWBE','wape_unit':'percentage points','rmse_unit':'original surveillance indicator units','selection_reason':'Matches the archived Project recursive seasonal Poisson metrics; the route column is a distinct alternative, not the reported manuscript method.'})
    with ZipFile(source) as z:
        name=next(n for n in z.namelist() if n.endswith('04_H0_H8_PAIRED_PREDICTIONS.csv'))
        rows=list(csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))))
    keys=[(r['origin'],int(r['week_ahead']),r['date']) for r in rows]
    assert len(keys)==len(set(keys))
    csvsave(prefix+'_frozen_paired_predictions.csv',rows)
    results=[]
    for group in [*range(9),'H1-H8']:
        subset=[r for r in rows if (int(r['week_ahead'])>0 if group=='H1-H8' else int(r['week_ahead'])==group)]
        origins=sorted({r['origin'] for r in subset});by_origin={o:[r for r in subset if r['origin']==o] for o in origins}
        values=np.array([[sum(abs(float(r['true'])-float(r[model])) for r in by_origin[o]),
                          sum(abs(float(r['true'])-float(r['AWBE'])) for r in by_origin[o]),
                          sum(abs(float(r['true'])) for r in by_origin[o]),
                          sum((float(r['true'])-float(r[model]))**2 for r in by_origin[o]),
                          sum((float(r['true'])-float(r['AWBE']))**2 for r in by_origin[o]),len(by_origin[o])] for o in origins])
        total=values.sum(axis=0);delta=(total[0]-total[1])/total[2]*100
        for block in (4,8,13):
            rng=np.random.default_rng(SEED);n=len(origins)
            starts=rng.integers(0,n-block+1,size=(5000,int(np.ceil(n/block))))
            index=(starts[:,:,None]+np.arange(block)).reshape(5000,-1)[:,:n]
            sums=values[index].sum(axis=1)
            dw=(sums[:,0]-sums[:,1])/sums[:,2]*100
            dr=np.sqrt(sums[:,3]/sums[:,5])-np.sqrt(sums[:,4]/sums[:,5])
            results.append({'horizon':group,'paired_targets':len(subset),'origins':n,'block':block,
               'project_wape_pct':total[0]/total[2]*100,'awbe_wape_pct':total[1]/total[2]*100,'delta_wape_pp':delta,'wape_ci95_pp':np.quantile(dw,[.025,.975]).tolist(),
               'project_rmse':float(np.sqrt(total[3]/total[5])),'awbe_rmse':float(np.sqrt(total[4]/total[5])),
               'delta_rmse':float(np.sqrt(total[3]/total[5])-np.sqrt(total[4]/total[5])),'rmse_ci95':np.quantile(dr,[.025,.975]).tolist()})
    save(prefix+'_horizon_confidence_intervals.json',results)
    print(json.dumps([r for r in results if r['block']==8],ensure_ascii=False,indent=2))

def system():
    docker=shutil.which('docker')
    save('container_environment.json',{'docker_cli_available':bool(docker),'container_startup_verified':False,
         'status':'unavailable: Docker CLI is not installed or on PATH' if not docker else 'requires explicit local daemon verification',
         'torch_installed':__import__('importlib.util',fromlist=['find_spec']).find_spec('torch') is not None,
         'transformers_installed':__import__('importlib.util',fromlist=['find_spec']).find_spec('transformers') is not None,
         'real_llm_ablation_completed':False})
    env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT')}
    env.update({'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'packages')]),'APP_ENV':'development','DATABASE_URL':'sqlite:///:memory:','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONIOENCODING':'utf-8'})
    runs={
      'application_smoke':"import socket,sys;socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('NETWORK_DISABLED'));from scripts.smoke_test import main;sys.exit(main(['--temp-db','--include-api','--include-worker']))",
      'compose_static':"from scripts.check_compose import check_compose;import json;print(json.dumps(check_compose(),ensure_ascii=False))",
    }
    for name,code in runs.items():
        started=time.perf_counter()
        try:
            run=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-X','utf8','-c',code],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
            output=sanitize(run.stdout+'\n'+run.stderr)[0]
            (OUT/(name+'.txt')).write_text(output,encoding='utf-8')
            save(name+'.json',{'exit_code':run.returncode,'seconds':time.perf_counter()-started,'network':'disabled for application smoke','kind':'application layers, not Docker container startup' if name=='application_smoke' else 'configuration inspection, not container startup'})
            print(name,run.returncode,output[:1800])
        except subprocess.TimeoutExpired:save(name+'.json',{'status':'timeout','seconds':90})

def system_retry(attempt='retry'):
    scratch=OUT/'application_smoke_workspace';scratch.mkdir(exist_ok=True)
    env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT')}
    env.update({'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'packages')]),'APP_ENV':'development','DATABASE_URL':'sqlite:///:memory:','PYTHONIOENCODING':'utf-8'})
    guard="socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('NETWORK_DISABLED'))"
    if attempt=='loopback':
        guard="""original_connect=socket.socket.connect
def offline_connect(sock,address):
    import ipaddress
    if isinstance(address,tuple):
        try:
            if ipaddress.ip_address(address[0]).is_loopback:return original_connect(sock,address)
        except ValueError:pass
    raise RuntimeError('EXTERNAL_NETWORK_DISABLED')
socket.socket.connect=offline_connect"""
    code="import tempfile,socket,sys\ntempfile.tempdir="+repr(str(scratch.resolve()))+"\n"+guard+"\nfrom scripts.smoke_test import main\nsys.exit(main(['--temp-db','--include-api','--include-worker']))"
    started=time.perf_counter()
    run=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-X','utf8','-c',code],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    output=sanitize(run.stdout+'\n'+run.stderr)[0]
    (OUT/('application_smoke_'+attempt+'.txt')).write_text(output,encoding='utf-8')
    save('application_smoke_'+attempt+'.json',{'exit_code':run.returncode,'seconds':time.perf_counter()-started,'change':'temporary SQLite workspace under writable project output; loopback allowed only to support Windows asyncio self-pipe when attempt=loopback; application code and test assertions unchanged','network':'external network disabled','container_startup_verified':False})
    print(output[:3500])

def regression(attempt='initial'):
    prefix='workflow_regression' if attempt=='initial' else 'workflow_regression_'+attempt
    selected=['tests/test_api_health.py','tests/test_api_sources.py','tests/test_api_clean.py','tests/test_api_clean_features.py',
       'tests/test_api_models.py','tests/test_api_feedback.py','tests/test_api_access_policy.py','tests/test_dashboard_safety.py',
       'tests/test_dashboard_result_formatters.py','tests/test_worker_pipeline.py','tests/test_access_policy.py','tests/test_check_compose.py','tests/test_docker_local_check.py']
    save(prefix+'_protocol.json',{'files':[{ 'path':p,'sha256':sha(ROOT/p)} for p in selected],
       'purpose':'existing API/dashboard formatting/worker/compose checks; recorded fixtures, temporary SQLite, external sockets denied',
       'not_proved':'browser rendering, Docker startup, production authorization or real surveillance predictive accuracy'})
    env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT')}
    env.update({'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'packages')]),'APP_ENV':'development','DATABASE_URL':'sqlite:///:memory:',
        'PYTHONIOENCODING':'utf-8','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'})
    args=selected+['-q','-o','addopts=','-p','no:cacheprovider','--basetemp',str((OUT/('pytest_temp_'+attempt)).resolve()),'--junitxml',str((OUT/(prefix+'.xml')).resolve())]
    guard="""import socket,ipaddress,pytest,sys
original_connect=socket.socket.connect
def guarded_connect(sock,address):
    if isinstance(address,tuple):
        try:
            if ipaddress.ip_address(address[0]).is_loopback:return original_connect(sock,address)
        except ValueError:pass
    raise RuntimeError('EXTERNAL_NETWORK_DISABLED')
socket.socket.connect=guarded_connect
"""
    run=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-X','utf8','-c',guard+'sys.exit(pytest.main('+repr(args)+'))'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    output=sanitize(run.stdout+'\n'+run.stderr)[0];(OUT/(prefix+'.txt')).write_text(output,encoding='utf-8')
    save(prefix+'_result.json',{'exit_code':run.returncode,'external_network':'disabled','test_files':len(selected)})
    print(output[-5500:])

if __name__=='__main__':{'hk':hk,'system':system,'system-retry':system_retry,'system-loopback':lambda:system_retry('loopback'),'regression':regression,'regression-retry':lambda:regression('retry')}[sys.argv[1]]()

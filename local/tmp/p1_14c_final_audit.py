from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE=r'''
import os,json,pathlib,hashlib,subprocess,datetime
from sqlalchemy import create_engine,text
PROJECT=pathlib.Path(os.environ['P1_14C_PROJECT']); OUT=pathlib.Path('/tmp/p1_14c_v2_preview_path')
def run(cmd, timeout=300):
 p=subprocess.run(cmd,shell=True,cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,executable='/bin/bash')
 return {'rc':p.returncode,'stdout':p.stdout[-8000:],'stderr':p.stderr[-4000:]}
def sha(path):
 h=hashlib.sha256();
 with open(PROJECT/path,'rb') as f:
  for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
 return h.hexdigest()
engine=create_engine(os.environ['APP_DATABASE_URL'],hide_parameters=True,pool_pre_ping=True)
report={'generated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
report['git']={'branch':run('git branch --show-current'),'head':run('git rev-parse HEAD'),'status':run('git status --porcelain=v2 -uall'),'diff_check':run('git diff --check',120)}
report['protected_hashes']={'dashboard_app.py':sha('src/epidemic_core/p1_12/dashboard_app.py'),'review_app.py':sha('src/epidemic_core/discovery/review_app.py')}
report['alembic']={'current':run('source scripts/database/_env.sh >/dev/null 2>&1; alembic current'),'heads':run('source scripts/database/_env.sh >/dev/null 2>&1; alembic heads')}
report['quality']={
 'ruff_changed':run('python -m ruff check src/epidemic_core/ebola/fetcher.py src/epidemic_core/p1_12/discovery.py migrations/versions/0027_p1_14c_v2_preview_path_grants.py tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py tests/test_p1_11_ebola_parsers.py',300),
 'pytest_p1_14c':run('python -m pytest tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py tests/test_p1_11_ebola_parsers.py -q',300),
 'pytest_existing_gate':run('python -m pytest tests/unit/test_p1_12_review_gate.py tests/unit/test_p1_12_trusted_and_permissions.py -q',600),
 'pip_check':run('python -m pip check',300),
 'secret_tls_scan':run("rg -n 'curl -k|--no-check-certificate|GIT_SSL_NO_VERIFY|--trusted-host|password\s*=|token\s*=|http_proxy=|https_proxy=' src/epidemic_core/ebola/fetcher.py src/epidemic_core/p1_12/discovery.py migrations/versions/0027_p1_14c_v2_preview_path_grants.py tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py tests/test_p1_11_ebola_parsers.py || true",120),
}
report['services']={
 'dashboard':run("if [ -f ../epidemic-dashboard-runtime/run/dashboard.pid ]; then pid=$(cat ../epidemic-dashboard-runtime/run/dashboard.pid); ps -p $pid -o pid=,cwd=,args=; fi; curl --silent --show-error --noproxy '*' --max-time 5 http://127.0.0.1:8020/readyz || true",60),
 'review':run("if [ -f ../epidemic-source-review-runtime/run/review.pid ]; then pid=$(cat ../epidemic-source-review-runtime/run/review.pid); ps -p $pid -o pid=,cwd=,args=; fi; curl --silent --show-error --noproxy '*' --max-time 5 http://127.0.0.1:8021/readyz || true",60),
 'proxy_residual':run("env | awk -F= '/^(http_proxy|https_proxy|HTTP_PROXY|HTTPS_PROXY|all_proxy|ALL_PROXY)=/{print $1}'",60),
}
with engine.connect() as c:
 report['db']={}
 report['db']['acl']= [dict(r) for r in c.execute(text("""
 SELECT 'violations_execute' item,has_function_privilege('epidemic_app','epidemic_p1_12.review_ready_v2_violations(uuid)','EXECUTE')::text val
 UNION ALL SELECT 'mark_execute',has_function_privilege('epidemic_app','epidemic_p1_12.mark_review_ready_v2(uuid,character)','EXECUTE')::text
 UNION ALL SELECT 'public_violations_execute', (SELECT (coalesce(p.proacl::text,'') LIKE '{=X/%' OR coalesce(p.proacl::text,'') LIKE '%,=X/%')::text FROM pg_proc p WHERE p.oid='epidemic_p1_12.review_ready_v2_violations(uuid)'::regprocedure)
 """)).mappings()]
 report['db']['objects']=[dict(r) for r in c.execute(text("""
 SELECT cv.content_sha256,sc.id::text candidate_id,cv.id::text version_id,rc.id::text review_case_id,rc.state,x.id::text extraction_run_id,
 ce.eligible,ce.candidate_preview_row_count,ce.mappable_person_metric_count,ce.preview_sha256,ce.eligibility_hash,
 epidemic_p1_12.review_ready_v2_violations(cv.id) violations
 FROM epidemic_discovery.source_candidate_versions cv
 JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
 JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id
 JOIN epidemic_p1_12.candidate_extraction_runs x ON x.candidate_version_id=cv.id
 JOIN epidemic_p1_12.candidate_eligibility_v2 ce ON ce.candidate_version_id=cv.id
 WHERE cv.content_sha256=ANY(:sha)
 ORDER BY cv.content_sha256
 """), {'sha':['ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc']}).mappings()]
 report['db']['counts']=[dict(r) for r in c.execute(text("""
 SELECT p.country_iso3,p.metric_code,count(*)::bigint rows,min(p.report_date)::text min_date,max(p.report_date)::text max_date,
 sum(CASE WHEN p.value_numeric IS NOT NULL THEN 1 ELSE 0 END)::bigint accepted,
 sum(CASE WHEN p.value_numeric IS NULL THEN 1 ELSE 0 END)::bigint rejected_or_quarantined
 FROM epidemic_p1_12.candidate_preview_rows_v2 p WHERE p.artifact_sha256=ANY(:sha)
 GROUP BY p.country_iso3,p.metric_code ORDER BY p.country_iso3,p.metric_code
 """), {'sha':['ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc']}).mappings()]
 report['db']['review_page_counts']=[dict(r) for r in c.execute(text("""
 SELECT state,count(*)::bigint FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.content_sha256=ANY(:sha) GROUP BY state ORDER BY state
 """), {'sha':['3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc','ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32']}).mappings()]
(OUT/'final_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'written':str(OUT/'final_audit.json'),'alembic':report['alembic']['current']['stdout'].strip(),'diff_check_rc':report['git']['diff_check']['rc'],'ruff_rc':report['quality']['ruff_changed']['rc'],'pip_check_rc':report['quality']['pip_check']['rc'],'review_readyz':report['services']['review']['rc'],'dashboard_readyz':report['services']['dashboard']['rc']},ensure_ascii=False))
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 remote='/tmp/p1_14c_v2_preview_path/final_audit.py'
 with client.open_sftp() as sftp:
  with sftp.open(remote,'w') as f:f.write(REMOTE)
 inner='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; P1_14C_PROJECT='+helper.q(project)+' python3 '+helper.q(remote)
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(inner)}",timeout=1200)
 print(out)
 if err: print(err,file=sys.stderr)
 if rc!=0: raise SystemExit(rc)
finally:
 client.close()







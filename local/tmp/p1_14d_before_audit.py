from __future__ import annotations
from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE = r'''
from __future__ import annotations
import os, json, pathlib, subprocess, hashlib, datetime
from sqlalchemy import create_engine, text
PROJECT=pathlib.Path(os.environ['P1_14D_PROJECT'])
OUT=pathlib.Path('/tmp/p1_14d_authenticated_ui_acceptance')
(OUT/'screenshots').mkdir(parents=True, exist_ok=True)
SHAS=['ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc']
def run(cmd, timeout=120):
 p=subprocess.run(cmd,shell=True,cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,executable='/bin/bash')
 return {'rc':p.returncode,'stdout':p.stdout[-20000:],'stderr':p.stderr[-8000:]}
def file_sha(rel):
 h=hashlib.sha256()
 with open(PROJECT/rel,'rb') as f:
  for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
 return h.hexdigest()
def digest(rows):
 return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def scalar(c, sql, params=None): return c.execute(text(sql), params or {}).scalar()
def rows(c, sql, params=None): return [dict(r) for r in c.execute(text(sql), params or {}).mappings().all()]
engine=create_engine(os.environ['APP_DATABASE_URL'], hide_parameters=True, pool_pre_ping=True)
report={'generated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'stage':'before'}
report['repository']={
 'root': run('git rev-parse --show-toplevel'),
 'branch': run('git branch --show-current'),
 'head': run('git rev-parse HEAD'),
 'status_porcelain_v2': run('git status --porcelain=v2 -uall', 180),
 'diff_0026': run('git diff -- migrations/versions/0026_p1_12_preview_view_grants.py'),
 'diff_check': run('git diff --check', 180),
}
report['protected_files']={
 'src/epidemic_core/p1_12/dashboard_app.py': file_sha('src/epidemic_core/p1_12/dashboard_app.py'),
 'src/epidemic_core/discovery/review_app.py': file_sha('src/epidemic_core/discovery/review_app.py'),
 'migrations/versions/0026_p1_12_preview_view_grants.py': file_sha('migrations/versions/0026_p1_12_preview_view_grants.py'),
 'migrations/versions/0027_p1_14c_v2_preview_path_grants.py': file_sha('migrations/versions/0027_p1_14c_v2_preview_path_grants.py'),
}
report['alembic']={'current':run('source scripts/database/_env.sh >/dev/null 2>&1; alembic current'), 'heads':run('source scripts/database/_env.sh >/dev/null 2>&1; alembic heads')}
report['services']={
 'review': run("pid=$(cat ../epidemic-source-review-runtime/run/review.pid 2>/dev/null || true); if [ -n \"$pid\" ]; then ps -p $pid -o pid=,lstart=,cwd=,args=; readlink /proc/$pid/cwd 2>/dev/null || true; fi; curl --silent --show-error --noproxy '*' --max-time 5 http://127.0.0.1:8021/readyz || true"),
 'dashboard': run("pid=$(cat ../epidemic-dashboard-runtime/run/dashboard.pid 2>/dev/null || true); if [ -n \"$pid\" ]; then ps -p $pid -o pid=,lstart=,cwd=,args=; readlink /proc/$pid/cwd 2>/dev/null || true; fi; curl --silent --show-error --noproxy '*' --max-time 5 http://127.0.0.1:8020/readyz || true"),
 'listen': run("ss -ltnp '( sport = :8021 or sport = :8020 )' 2>/dev/null || true"),
 'proxy_residual': run("env | awk -F= '/^(http_proxy|https_proxy|HTTP_PROXY|HTTPS_PROXY|all_proxy|ALL_PROXY)=/{print $1}'"),
}
with engine.connect() as c:
 trans=c.begin()
 try:
  c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
  c.execute(text("SET LOCAL statement_timeout='30s'"))
  c.execute(text("SET LOCAL idle_in_transaction_session_timeout='60s'"))
  report['transaction_read_only']=scalar(c,'SHOW transaction_read_only')
  report['runtime_role']=rows(c,"SELECT session_user,current_user,current_database() database,current_schema() schema,current_setting('is_superuser',true) is_superuser")
  report['gate']={
   'functions': rows(c,"""SELECT n.nspname schema,p.oid::regprocedure::text identity_signature,pg_get_userbyid(p.proowner) owner,p.prosecdef security_definer,p.provolatile volatility,p.proacl,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='epidemic_p1_12' AND p.proname='review_ready_v2_violations'"""),
   'acl': rows(c,"""SELECT 'epidemic_app_execute' item, has_function_privilege('epidemic_app','epidemic_p1_12.review_ready_v2_violations(uuid)','EXECUTE')::text val UNION ALL SELECT 'public_execute',(SELECT (coalesce(p.proacl::text,'') LIKE '{=X/%' OR coalesce(p.proacl::text,'') LIKE '%,=X/%')::text FROM pg_proc p WHERE p.oid='epidemic_p1_12.review_ready_v2_violations(uuid)'::regprocedure)"""),
   'deps': rows(c,"""SELECT cl.relkind,ns.nspname schema,cl.relname name FROM pg_depend d JOIN pg_proc p ON p.oid=d.objid JOIN pg_class cl ON cl.oid=d.refobjid JOIN pg_namespace ns ON ns.oid=cl.relnamespace WHERE p.oid='epidemic_p1_12.review_ready_v2_violations(uuid)'::regprocedure ORDER BY 2,3"""),
  }
  chain=rows(c,"""
   SELECT rq.id::text discovery_request_id,dr.id::text run_id,dr.state run_state,sc.id::text candidate_id,cv.id::text version_id,cv.content_sha256,
    qa.id::text artifact_id,qa.byte_size,qa.mime_type,qa.storage_key,qa.sha256 artifact_sha256,
    x.id::text extraction_run_id,x.state extraction_state,x.parsed_row_count,x.extraction_sha256,
    ce.eligible,ce.candidate_preview_row_count,ce.mappable_person_metric_count,ce.preview_sha256,ce.eligibility_hash,
    rc.id::text review_case_id,rc.state review_state,rc.etag,
    epidemic_p1_12.review_ready_v2_violations(cv.id) gate_violations
   FROM epidemic_discovery.source_candidate_versions cv
   JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
   JOIN epidemic_discovery.discovery_runs dr ON dr.id=sc.discovery_run_id
   LEFT JOIN epidemic_p1_12.discovery_requests_v2 rq ON rq.discovery_run_id=dr.id
   JOIN epidemic_discovery.quarantine_artifacts qa ON qa.candidate_version_id=cv.id
   JOIN epidemic_p1_12.candidate_extraction_runs x ON x.candidate_version_id=cv.id
   JOIN epidemic_p1_12.candidate_eligibility_v2 ce ON ce.candidate_version_id=cv.id
   JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id
   WHERE cv.content_sha256=ANY(:sha)
   ORDER BY cv.content_sha256
  """, {'sha':SHAS})
  report['candidate_chain']={'rows':chain,'count':len(chain),'digest':digest(chain)}
  counts=rows(c,"""
   SELECT p.country_iso3,p.metric_code,count(*)::bigint row_count,min(p.report_date)::text min_date,max(p.report_date)::text max_date,
    sum(CASE WHEN p.value_numeric IS NOT NULL THEN 1 ELSE 0 END)::bigint accepted,
    sum(CASE WHEN p.value_numeric IS NULL THEN 1 ELSE 0 END)::bigint rejected_or_quarantined,
    count(*) FILTER (WHERE p.source_row_locator IS NOT NULL AND p.source_row_locator<>'')::bigint lineage_rows
   FROM epidemic_p1_12.candidate_preview_rows_v2 p WHERE p.artifact_sha256=ANY(:sha)
   GROUP BY p.country_iso3,p.metric_code ORDER BY p.country_iso3,p.metric_code
  """, {'sha':SHAS})
  report['preview_counts']={'rows':counts,'total':sum(int(r['row_count']) for r in counts),'digest':digest(counts)}
  review_state=rows(c,"""SELECT rc.state,count(*)::bigint count FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.content_sha256=ANY(:sha) GROUP BY rc.state ORDER BY rc.state""", {'sha':SHAS})
  report['review_state_distribution']={'rows':review_state,'digest':digest(review_state)}
  # deterministic spot checks: first/middle/last report date per country and both metrics
  spot=rows(c,"""
   WITH ranked_dates AS (
    SELECT country_iso3, report_date, row_number() OVER(PARTITION BY country_iso3 ORDER BY report_date) rn, count(*) OVER(PARTITION BY country_iso3) n
    FROM (SELECT DISTINCT country_iso3,report_date FROM epidemic_p1_12.candidate_preview_rows_v2 WHERE artifact_sha256=ANY(:sha)) d
   ), chosen AS (
    SELECT country_iso3,report_date FROM ranked_dates WHERE rn=1 OR rn=(n+1)/2 OR rn=n
   )
   SELECT p.country_iso3,p.report_date::text,p.metric_code,p.value_numeric::text,p.unit,p.aggregation_type,p.case_definition,p.artifact_sha256,p.source_row_locator,p.raw_value,p.normalized_value,p.source_fields,p.mapping_json,p.quality_flags
   FROM epidemic_p1_12.candidate_preview_rows_v2 p JOIN chosen c ON c.country_iso3=p.country_iso3 AND c.report_date=p.report_date
   WHERE p.artifact_sha256=ANY(:sha)
   ORDER BY p.country_iso3,p.report_date,p.metric_code
  """, {'sha':SHAS})
  report['spot_checks']={'rows':spot,'count':len(spot),'digest':digest(spot)}
  invariants={}
  invariant_sql={
   'review_decisions':'SELECT * FROM epidemic_discovery.source_review_decisions ORDER BY id',
   'promotion_runs':'SELECT * FROM epidemic_discovery.source_promotion_runs ORDER BY id',
   'promotion_events':'SELECT * FROM epidemic_discovery.source_promotion_events ORDER BY id',
   'snapshots':'SELECT * FROM epidemic.dataset_snapshots ORDER BY id',
   'publications':'SELECT * FROM epidemic_discovery.p1_9_publications ORDER BY id',
   'historical_schedules':'SELECT * FROM epidemic_p1_12.trusted_update_schedules ORDER BY id',
   'normalized_observations':'SELECT * FROM epidemic.normalized_observations ORDER BY id',
   'query_requests':'SELECT * FROM epidemic.query_requests ORDER BY id',
  }
  for name,sql in invariant_sql.items():
   rr=rows(c,sql); invariants[name]={'count':len(rr),'ids':[x.get('id') for x in rr],'digest':digest(rr)}
  report['invariants']=invariants
  # contamination indicators from full pytest attempt: query_requests on production > expected fixture-free baseline, test marker states.
  contam=rows(c,"""
    SELECT 'query_requests_total' indicator,count(*)::text value FROM epidemic.query_requests
    UNION ALL SELECT 'test_isolation_discovery_runs',count(*)::text FROM epidemic_discovery.discovery_runs WHERE terminal_reason_code='TEST_FIXTURE_ISOLATION' OR request_json::text ILIKE '%test%fixture%'
    UNION ALL SELECT 'test_generated_snapshots',count(*)::text FROM epidemic.dataset_snapshots ds WHERE to_jsonb(ds)::text ILIKE '%fixture%' OR to_jsonb(ds)::text ILIKE '%TEST_FIXTURE%'
  """)
  report['production_test_contamination_probe']={'rows':contam,'digest':digest(contam)}
  trans.commit()
 except Exception:
  trans.rollback(); raise
(OUT/'database_before.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
# Closeout read from P1-14C files if present.
closeout={'p1_14c_files_present':{}, 'summary':{}}
for name in ['diagnosis.json','final_audit.json','materialization.json','migration_isolation.json','production_migration_apply.json']:
 path=pathlib.Path('/tmp/p1_14c_v2_preview_path')/name
 closeout['p1_14c_files_present'][name]=path.exists()
 if path.exists():
  try: closeout['summary'][name]=json.loads(path.read_text())
  except Exception as exc: closeout['summary'][name]={'read_error':str(exc)}
(OUT/'p1_14c_closeout.json').write_text(json.dumps(closeout,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'written':str(OUT/'database_before.json'),'chain_count':len(chain),'preview_total':report['preview_counts']['total'],'states':review_state,'contamination_probe':contam},ensure_ascii=False))
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    remote='/tmp/p1_14d_authenticated_ui_acceptance/before_audit.py'
    helper.p111.p110r3.previous.p19.a100.checked(client, 'mkdir -p /tmp/p1_14d_authenticated_ui_acceptance/screenshots')
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f: f.write(REMOTE)
    inner='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; P1_14D_PROJECT='+helper.q(project)+' python3 '+helper.q(remote)
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(inner)}",timeout=600)
    print(out)
    if err: print(err,file=sys.stderr)
    if rc: raise SystemExit(rc)
finally:
    client.close()



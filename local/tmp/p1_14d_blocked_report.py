from pathlib import Path
import sys,json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE=r'''
import json,pathlib,datetime
OUT=pathlib.Path('/tmp/p1_14d_authenticated_ui_acceptance')

def read_json(name):
 p=OUT/name
 return json.loads(p.read_text()) if p.exists() else None
before=read_json('database_before.json') or {}
contam=read_json('production_test_contamination_probe.json') or {}
status='BLOCKED_PRODUCTION_DATABASE_TEST_CONTAMINATION'
report={
 'status':status,
 'data_pipeline_status':'P1_14C_WEST_AFRICA_V2_REVIEW_CASES_READY_PREVIEW_ONLY',
 'ui_acceptance':'NOT_ATTEMPTED_BLOCKED_BY_PRODUCTION_DATABASE_TEST_CONTAMINATION',
 'scope':'P1_14C_CLOSEOUT_AND_AUTHENTICATED_READ_ONLY',
 'generated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'repository':{
  'branch':(before.get('repository',{}).get('branch',{}).get('stdout','')).strip(),
  'head':(before.get('repository',{}).get('head',{}).get('stdout','')).strip(),
  'revision':(before.get('alembic',{}).get('current',{}).get('stdout','')).strip(),
  'git_status_porcelain_v2_present':bool(before.get('repository',{}).get('status_porcelain_v2',{}).get('stdout','')),
  'diff_0026_empty':not bool(before.get('repository',{}).get('diff_0026',{}).get('stdout','')),
 },
 'protected_files':before.get('protected_files',{}),
 'services':{
  'p1_14c_restarts_reported':['review','dashboard'],
  'restarted_this_stage':[],
  'review_readyz_checked': '"ready":true' in before.get('services',{}).get('review',{}).get('stdout',''),
  'dashboard_readyz_checked': '"ready":true' in before.get('services',{}).get('dashboard',{}).get('stdout',''),
 },
 'p1_14c_closeout':{
  'diagnosis_preceded_changes': True,
  'migration_0026_unchanged': not bool(before.get('repository',{}).get('diff_0026',{}).get('stdout','')),
  'migration_0027_acl_only': True,
  'upgrade_downgrade_upgrade_verified': True,
  'idempotence_verified': True,
  'concurrency_verified': True,
  'atomicity_verified': True,
  'production_test_contamination_found': True,
 },
 'authentication':{
  'method':'not_attempted_after_blocker',
  'credentials_exposed':False,
  'auth_artifacts_saved':False,
 },
 'database_before':{
  'candidate_chain_count':before.get('candidate_chain',{}).get('count'),
  'candidate_chain_digest':before.get('candidate_chain',{}).get('digest'),
  'preview_total':before.get('preview_counts',{}).get('total'),
  'preview_digest':before.get('preview_counts',{}).get('digest'),
  'review_state_distribution':before.get('review_state_distribution',{}).get('rows'),
  'spot_check_count':before.get('spot_checks',{}).get('count'),
  'invariants':{k:{'count':v.get('count'),'digest':v.get('digest')} for k,v in before.get('invariants',{}).items()},
  'production_test_contamination_probe':before.get('production_test_contamination_probe'),
 },
 'review_summary':{
  'rendered_text':'',
  'database_counts':{'candidate_versions':3,'review_cases':3,'preview_rows':910,'review_ready':3},
  'ui_counts':{},
  'pending_not_trusted_statement_present':False,
 },
 'cases':before.get('candidate_chain',{}).get('rows',[]),
 'spot_checks':before.get('spot_checks',{}).get('rows',[]),
 'http_audit':{'attempted':False,'reason':'blocked_before_authenticated_ui_due_to_production_test_contamination'},
 'database_after':{},
 'get_side_effect_check':{'counts_stable':False,'ids_stable':False,'states_stable':False,'digests_stable':False,'verified':False},
 'database_invariants':{
  'decision_delta':0,'approval_delta':0,'promotion_delta':0,'promotion_event_delta':0,
  'trusted_delta':0,'snapshot_delta':0,'publication_delta':0,'historical_schedule_delta':0,
 },
 'tests':{
  'p1_14c_focused':'3 passed',
  'p1_12_gate_permissions_regression':'45 passed',
  'p1_11_p1_14c_combined_focused':'81 passed',
  'ruff':'passed',
  'git_diff_check':'passed',
  'pip_check':'passed',
  'full_pytest':{
   'command':'python -m pytest -q',
   'result':'failed on non-disposable production database',
   'summary':'3 failed, 327 passed, 1 warning, 13 errors in 12.31s',
   'direct_p1_14c_related_unresolved_failure':False,
   'could_write_production_database':True,
   'not_repeated_in_p1_14d':True,
   'failure_classes':['production_database_not_disposable','insufficient_privilege_for_test_isolation','preexisting_or_test_inserted_query_requests','production_data_row_count_assumptions'],
  },
 },
 'evidence':{
  'remote_dir':str(OUT),
  'files':['database_before.json','p1_14c_closeout.json','production_test_contamination_probe.json','report.json','report.md'],
  'screenshots_saved':[],
 },
 'approval_boundary':{
  'review_decision_created':False,
  'review_decision_modified':False,
  'promotion_executed':False,
  'trusted_data_modified':False,
 },
 'warnings':['Authenticated UI acceptance was not attempted because P1-14D requires stopping on suspected production database test contamination.'],
 'unknowns':['Exact cleanup/remediation plan for query_requests test contamination requires separate user authorization; no deletion or repair was performed.'],
 'blockers':[{
  'code':'BLOCKED_PRODUCTION_DATABASE_TEST_CONTAMINATION',
  'evidence':{
   'query_requests_count':contam.get('query_requests_count'),
   'query_requests_with_test_marker':sum(1 for r in contam.get('rows',[]) if r.get('has_test_marker')),
   'latest_query_request_created_at':contam.get('rows',[])[-1].get('created_at') if contam.get('rows') else None,
   'latest_query_request_prefix':contam.get('rows',[])[-1].get('row_prefix') if contam.get('rows') else None,
  },
 }],
}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
md=[]
md.append('# P1-14D Authenticated UI Acceptance - BLOCKED')
md.append('')
md.append(f"Status: `{status}`")
md.append('')
md.append('P1-14C 数据链只读核验通过：Candidate/version=3、Review Case=3、Preview rows=910、REVIEW_READY=3。')
md.append('')
md.append('但是正式库污染探针发现 `epidemic.query_requests` 当前为 9 行，其中 4 行包含 test/fixture 标记；这与 P1-14C 全量 pytest 在非 disposable 正式库上失败的窗口一致。根据 P1-14D 规则，必须停止，不得继续登录态 UI 验收，也不得删除或修复数据。')
md.append('')
md.append('本轮未修改代码、配置、migration、正式业务数据；未重启 Review/Dashboard/PostgreSQL/vLLM/worker；未创建 Review Decision、未 Promotion、未写 Trusted。')
(OUT/'report.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
print(json.dumps({'status':status,'report':str(OUT/'report.json'),'query_requests_count':contam.get('query_requests_count'),'test_marker_count':sum(1 for r in contam.get('rows',[]) if r.get('has_test_marker'))},ensure_ascii=False))
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 remote='/tmp/p1_14d_authenticated_ui_acceptance/write_blocked_report.py'
 with client.open_sftp() as sftp:
  with sftp.open(remote,'w') as f:f.write(REMOTE)
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"python3 {helper.q(remote)}",timeout=120)
 print(out)
 if err: print(err,file=sys.stderr)
 if rc: raise SystemExit(rc)
finally:
 client.close()

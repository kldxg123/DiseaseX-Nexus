from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE=r'''
import os,json,pathlib,datetime,subprocess,concurrent.futures
from sqlalchemy import create_engine,text
from epidemic_core.ebola.fetcher import OfficialSourceFetcher
from epidemic_core.p1_12.discovery import CandidateDiscoveryService, WEST_AFRICA_CDC_STACKS_SPECS
PROJECT=pathlib.Path(os.environ['P1_14C_PROJECT']); OUT=pathlib.Path('/tmp/p1_14c_v2_preview_path')
engine=create_engine(os.environ['APP_DATABASE_URL'], hide_parameters=True, pool_pre_ping=True)
EXPECTED={
 'GIN':'ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32',
 'LBR':'3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee',
 'SLE':'9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc',
}
SAFETY_SQLS={
 'review_decisions':'SELECT id::text FROM epidemic_discovery.source_review_decisions ORDER BY id',
 'promotions':'SELECT id::text FROM epidemic_discovery.source_promotion_runs ORDER BY id',
 'promotion_events':'SELECT id::text FROM epidemic_discovery.source_promotion_events ORDER BY id',
 'snapshots':'SELECT id::text FROM epidemic.dataset_snapshots ORDER BY id',
 'publications':'SELECT id::text FROM epidemic_discovery.p1_9_publications ORDER BY id',
 'historical_schedules':'SELECT id::text FROM epidemic_p1_12.trusted_update_schedules ORDER BY id',
 'trusted_ebola':'SELECT id::text FROM epidemic.normalized_observations ORDER BY id',
}
def digest_rows(rows):
 import hashlib
 raw=json.dumps(rows, sort_keys=True, separators=(',',':')).encode()
 return hashlib.sha256(raw).hexdigest()
def safety_snapshot():
 out={}
 with engine.connect() as c:
  for name,sql in SAFETY_SQLS.items():
   rows=[r[0] for r in c.execute(text(sql)).all()]
   out[name]={'count':len(rows),'ids':rows,'digest':digest_rows(rows)}
 return out
def west_counts():
 with engine.connect() as c:
  return dict(c.execute(text("""
  SELECT 'versions' name,count(*)::bigint n FROM epidemic_discovery.source_candidate_versions WHERE content_sha256 = ANY(:sha)
  UNION ALL SELECT 'review_cases',count(*) FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.content_sha256 = ANY(:sha)
  UNION ALL SELECT 'preview_rows_v2',count(*) FROM epidemic_p1_12.candidate_preview_rows_v2 WHERE artifact_sha256 = ANY(:sha)
  UNION ALL SELECT 'eligibility',count(*) FROM epidemic_p1_12.candidate_eligibility_v2 ce JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=ce.candidate_version_id WHERE cv.content_sha256 = ANY(:sha)
  """).bindparams(sha=list(EXPECTED.values()))).all())
def run_spec(spec):
 fetcher=OfficialSourceFetcher(set(spec.official_domains), max_bytes=4*1024*1024, min_host_interval=0.1, user_agent='python-urllib/3.12', use_environment_proxy=True)
 service=CandidateDiscoveryService(engine, PROJECT)
 service.fetcher=fetcher
 return service.run_one(spec)
def manifest_dict(m):
 return {k:(str(v) if k.endswith('_id') or k in {'request_id','run_id','candidate_id','candidate_version_id','review_case_id','artifact_id','extraction_run_id'} else v) for k,v in m.__dict__.items()}
result={'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'before_safety':safety_snapshot(), 'before_counts':west_counts(), 'runs':[]}
try:
 canary=run_spec(WEST_AFRICA_CDC_STACKS_SPECS[0]); result['runs'].append({'phase':'canary','manifest':manifest_dict(canary)})
 if canary.artifact_sha256 != EXPECTED['GIN']:
  raise RuntimeError('BLOCKED_SOURCE_ARTIFACT_DRIFT:GIN')
 after_canary=west_counts(); result['after_canary_counts']=after_canary
 canary2=run_spec(WEST_AFRICA_CDC_STACKS_SPECS[0]); result['runs'].append({'phase':'canary_rerun','manifest':manifest_dict(canary2)})
 result['after_canary_rerun_counts']=west_counts()
 if manifest_dict(canary)!=manifest_dict(canary2):
  raise RuntimeError('CANARY_IDEMPOTENCE_MANIFEST_DRIFT')
 for spec in WEST_AFRICA_CDC_STACKS_SPECS[1:]:
  m=run_spec(spec); result['runs'].append({'phase':'country','manifest':manifest_dict(m)})
  if m.artifact_sha256 != EXPECTED[spec.country_iso3]:
   raise RuntimeError('BLOCKED_SOURCE_ARTIFACT_DRIFT:'+spec.country_iso3)
 result['after_all_counts']=west_counts()
 # Idempotent concurrent readback after completion: both executions must return the same existing chain and add no rows.
 before_concurrent=west_counts()
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
  pair=list(pool.map(run_spec, [WEST_AFRICA_CDC_STACKS_SPECS[2], WEST_AFRICA_CDC_STACKS_SPECS[2]]))
 result['concurrent_completed_rerun']=[manifest_dict(x) for x in pair]
 result['after_concurrent_counts']=west_counts()
 if result['after_concurrent_counts'] != before_concurrent:
  raise RuntimeError('CONCURRENT_IDEMPOTENCE_COUNT_DRIFT')
 result['after_safety']=safety_snapshot()
 result['safety_delta']={k: result['after_safety'][k]['count']-result['before_safety'][k]['count'] for k in result['before_safety']}
 if any(v!=0 for v in result['safety_delta'].values()):
  raise RuntimeError('SAFETY_INVARIANT_VIOLATION')
 with engine.connect() as c:
  rows=c.execute(text("""
    SELECT p.country_iso3,p.metric_code,count(*)::bigint rows,min(p.report_date)::text min_date,max(p.report_date)::text max_date,
      sum(CASE WHEN p.value_numeric IS NOT NULL THEN 1 ELSE 0 END)::bigint accepted
    FROM epidemic_p1_12.candidate_preview_rows_v2 p
    WHERE p.artifact_sha256 = ANY(:sha)
    GROUP BY p.country_iso3,p.metric_code ORDER BY p.country_iso3,p.metric_code
  """).bindparams(sha=list(EXPECTED.values()))).mappings().all()
  result['preview_accounting']=[dict(r) for r in rows]
  samples=c.execute(text("""
    SELECT p.country_iso3,p.metric_code,p.report_date::text,p.value_numeric,p.source_row_locator,p.raw_value
    FROM epidemic_p1_12.candidate_preview_rows_v2 p
    WHERE p.artifact_sha256 = ANY(:sha)
    ORDER BY p.country_iso3,p.row_ordinal
    LIMIT 12
  """).bindparams(sha=list(EXPECTED.values()))).mappings().all()
  result['sample_preview_rows']=[dict(r) for r in samples]
 result['status']='P1_14C_WEST_AFRICA_V2_REVIEW_CASES_READY_PREVIEW_ONLY'
except Exception as exc:
 result['status']='FAILED'
 result['error']=str(exc)
 raise
finally:
 result['finished_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 (OUT/'materialization.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
 print(json.dumps({'status':result.get('status'), 'error':result.get('error'), 'after_all_counts':result.get('after_all_counts'), 'safety_delta':result.get('safety_delta')}, ensure_ascii=False, default=str))
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    remote='/tmp/p1_14c_v2_preview_path/materialize.py'
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f:f.write(REMOTE)
    inner='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; if [ -r /etc/network_turbo ]; then source /etc/network_turbo >/dev/null 2>&1 || true; fi; P1_14C_PROJECT='+helper.q(project)+' python3 '+helper.q(remote)
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client, f"bash -lc {helper.q(inner)}", timeout=1200)
    print(out)
    if err: print(err, file=sys.stderr)
    if rc!=0: raise SystemExit(rc)
finally:
    client.close()






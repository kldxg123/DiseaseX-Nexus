from pathlib import Path
import sys, json, textwrap
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE = r'''
import os,json,subprocess,pathlib,datetime
from sqlalchemy import create_engine,text
PROJECT=pathlib.Path(os.environ['P1_14C_PROJECT']); OUT=pathlib.Path('/tmp/p1_14c_v2_preview_path')
engine=create_engine(os.environ['APP_DATABASE_URL'], hide_parameters=True, pool_pre_ping=True)
def q(sql):
    try:
        with engine.connect() as c:
            c.execute(text("SET statement_timeout='30s'"))
            return {'ok':True,'rows':[dict(r) for r in c.execute(text(sql)).mappings().all()]}
    except Exception as e:
        orig=getattr(e,'orig',None)
        return {'ok':False,'type':type(e).__name__,'sqlstate':getattr(orig,'sqlstate',None) or getattr(orig,'pgcode',None),'error':str(e)[:2000]}
res={}
res['role']=q("SELECT session_user,current_user,current_database() database,current_schema() schema,current_setting('is_superuser',true) is_superuser")
res['privileges']=q("""
SELECT 'schema_usage' item, has_schema_privilege(current_user,'epidemic_p1_12','USAGE')::text val
UNION ALL SELECT 'violations_execute', has_function_privilege(current_user,'epidemic_p1_12.review_ready_v2_violations(uuid)','EXECUTE')::text
UNION ALL SELECT 'mark_execute', has_function_privilege(current_user,'epidemic_p1_12.mark_review_ready_v2(uuid,character)','EXECUTE')::text
UNION ALL SELECT 'source_review_cases_insert', has_table_privilege(current_user,'epidemic_discovery.source_review_cases','INSERT')::text
UNION ALL SELECT 'preview_rows_insert', has_table_privilege(current_user,'epidemic_p1_12.candidate_preview_rows_v2','INSERT')::text
UNION ALL SELECT 'extraction_insert', has_table_privilege(current_user,'epidemic_p1_12.candidate_extraction_runs','INSERT')::text
""")
res['functions']=q("""
SELECT n.nspname schema,p.oid::regprocedure::text identity_signature,pg_get_userbyid(p.proowner) owner,l.lanname language,p.provolatile volatility,p.prosecdef security_definer,p.proacl acl,p.proconfig
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
WHERE n.nspname='epidemic_p1_12' AND p.proname IN ('review_ready_v2_violations','mark_review_ready_v2','review_ready_v2_guard') ORDER BY 2
""")
res['function_defs']=q("SELECT p.oid::regprocedure::text identity_signature, pg_get_functiondef(p.oid) functiondef FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='epidemic_p1_12' AND p.proname IN ('review_ready_v2_violations','mark_review_ready_v2') ORDER BY 1")
res['call_violations_zero']=q("SELECT epidemic_p1_12.review_ready_v2_violations('00000000-0000-0000-0000-000000000000'::uuid) violations")
res['counts']=q("""
SELECT 'discovery_runs_p114b' name,count(*)::bigint n FROM epidemic_discovery.discovery_runs WHERE request_json::text LIKE '%p1-14b%'
UNION ALL SELECT 'search_results_p114b',count(*) FROM epidemic_discovery.source_discovery_search_results WHERE provider_id='cdc-stacks-direct' OR metadata::text LIKE '%p1-14b%'
UNION ALL SELECT 'fetches_stacks',count(*) FROM epidemic_discovery.candidate_fetches WHERE domain='stacks.cdc.gov'
UNION ALL SELECT 'west_africa_versions',count(*) FROM epidemic_discovery.source_candidate_versions WHERE content_sha256 IN ('ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc')
UNION ALL SELECT 'west_africa_review_cases',count(*) FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.content_sha256 IN ('ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc')
UNION ALL SELECT 'west_africa_preview_v2',count(*) FROM epidemic_p1_12.candidate_preview_rows_v2 WHERE artifact_sha256 IN ('ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc')
UNION ALL SELECT 'review_decisions',count(*) FROM epidemic_discovery.source_review_decisions
UNION ALL SELECT 'promotions',count(*) FROM epidemic_discovery.source_promotion_runs
UNION ALL SELECT 'trusted_ebola',count(*) FROM epidemic_core.observations WHERE disease_id='ebola-disease'
""")
res['generated_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
(OUT/'app_runtime_diagnosis.json').write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(json.dumps({'written':str(OUT/'app_runtime_diagnosis.json'), 'role':res['role'], 'privileges':res['privileges'], 'call':res['call_violations_zero']}, ensure_ascii=False, default=str))
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    remote='/tmp/p1_14c_v2_preview_path/app_diag.py'
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f: f.write(REMOTE)
    cmd=f"bash -lc {helper.q('cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; P1_14C_PROJECT='+helper.q(project)+' python3 '+helper.q(remote))}"
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,cmd,timeout=300)
    print(out)
    if err: print(err, file=sys.stderr)
finally:
    client.close()

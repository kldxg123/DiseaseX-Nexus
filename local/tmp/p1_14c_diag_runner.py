from __future__ import annotations
import json, textwrap
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper

REMOTE = r'''#!/usr/bin/env python3
from __future__ import annotations
import os, sys, json, subprocess, hashlib, pathlib, datetime, traceback

OUT = pathlib.Path('/tmp/p1_14c_v2_preview_path')
OUT.mkdir(parents=True, exist_ok=True)
PROJECT = pathlib.Path(os.environ['P1_14C_PROJECT'])

def run(cmd, cwd=None, timeout=60):
    p = subprocess.run(cmd, shell=True, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, executable='/bin/bash')
    return {'rc': p.returncode, 'stdout': p.stdout[-20000:], 'stderr': p.stderr[-12000:], 'cmd': cmd if 'PASSWORD' not in cmd else '<redacted>'}

def sha_file(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()

def env_from_file(path):
    env = {}
    if not pathlib.Path(path).exists(): return env
    for line in pathlib.Path(path).read_text().splitlines():
        s=line.strip()
        if not s or s.startswith('#') or '=' not in s: continue
        k,v=s.split('=',1)
        v=v.strip().strip('"').strip("'")
        env[k.strip()] = v
    return env

def pg_probe(dsn_name, dsn, sql, timeout=60):
    if not dsn:
        return {'ok': False, 'error': f'{dsn_name}_missing'}
    code = r"""
import os, json, traceback
from sqlalchemy import create_engine, text
name=os.environ['DSN_NAME']; dsn=os.environ['DSN_VALUE']; sql=os.environ['SQL_TEXT']
out={'dsn_name':name}
try:
    e=create_engine(dsn, hide_parameters=True, pool_pre_ping=True)
    with e.connect() as c:
        c.execute(text("SET statement_timeout='30s'"))
        rows=c.execute(text(sql)).mappings().all()
        out['ok']=True; out['rows']=[dict(r) for r in rows]
except Exception as exc:
    out['ok']=False; out['error_type']=type(exc).__name__; out['error']=str(exc).replace(dsn,'<dsn>')[:4000]
    orig=getattr(exc,'orig',None)
    out['sqlstate']=getattr(orig,'sqlstate',None) or getattr(orig,'pgcode',None)
print(json.dumps(out, default=str, ensure_ascii=False))
"""
    env=os.environ.copy(); env.update({'DSN_NAME':dsn_name,'DSN_VALUE':dsn,'SQL_TEXT':sql})
    py=str(PROJECT.parent/'conda/envs/epidemic-core/bin/python')
    p=subprocess.run([py,'-c',code], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {'ok':False,'rc':p.returncode,'stdout':p.stdout[-1000:],'stderr':p.stderr[-2000:]}

report={'generated_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'redaction':'paths retained; secrets/dsns omitted'}
try:
    report['project_exists']=PROJECT.exists()
    report['pwd']=str(PROJECT)
    report['git']={
      'root': run('git rev-parse --show-toplevel', PROJECT),
      'branch': run('git branch --show-current', PROJECT),
      'head': run('git rev-parse HEAD', PROJECT),
      'status_porcelain_v2': run('git status --porcelain=v2 -uall', PROJECT, 120),
      'diff_name_status': run('git diff --name-status', PROJECT, 120),
    }
    protected=['src/epidemic_core/p1_12/dashboard_app.py','src/epidemic_core/discovery/review_app.py']
    report['protected_hashes']={p: sha_file(PROJECT/p) if (PROJECT/p).exists() else None for p in protected}
    report['alembic']={
      'current': run('source scripts/database/_env.sh >/dev/null 2>&1 || true; alembic current', PROJECT, 120),
      'heads': run('source scripts/database/_env.sh >/dev/null 2>&1 || true; alembic heads', PROJECT, 120),
    }
    report['services']={
      'dashboard': run("if [ -f ../epidemic-dashboard-runtime/run/dashboard.pid ]; then pid=$(cat ../epidemic-dashboard-runtime/run/dashboard.pid); ps -p $pid -o pid=,cwd=,args=; fi; curl --silent --show-error --noproxy '*' --max-time 5 http://127.0.0.1:8020/readyz || true", PROJECT, 30),
      'review': run("if [ -f ../epidemic-source-review-runtime/run/review.pid ]; then pid=$(cat ../epidemic-source-review-runtime/run/review.pid); ps -p $pid -o pid=,cwd=,args=; fi; curl --silent --show-error --noproxy '*' --max-time 5 http://127.0.0.1:8021/readyz || true", PROJECT, 30),
    }
    app_env={}
    for f in ['../epidemic-source-review-runtime/secrets/review.env','../epidemic-dashboard-runtime/secrets/dashboard.env','../epidemic-runtime/secrets/app.env','../epidemic-postgres-runtime/secrets/database.env','../epidemic-daily-update-runtime/secrets/daily_update.env','.secrets/p1_1.env','.secrets/postgres.env','.secrets/preview.env','.secrets/trusted_ingest.env']:
        app_env[f]=env_from_file(PROJECT/f)
    dsns={
      'review': app_env['../epidemic-source-review-runtime/secrets/review.env'].get('REVIEW_DATABASE_URL'),
      'dashboard': app_env['../epidemic-dashboard-runtime/secrets/dashboard.env'].get('DASHBOARD_DATABASE_URL'),
      'app': app_env['../epidemic-runtime/secrets/app.env'].get('APP_DATABASE_URL') or app_env['../epidemic-daily-update-runtime/secrets/daily_update.env'].get('APP_DATABASE_URL') or app_env['.secrets/p1_1.env'].get('APP_DATABASE_URL'),
      'migration': os.environ.get('MIGRATION_DATABASE_URL') or app_env['../epidemic-postgres-runtime/secrets/database.env'].get('MIGRATION_DATABASE_URL') or app_env['.secrets/postgres.env'].get('MIGRATION_DATABASE_URL'),
    }
    report['dsn_presence']={k: bool(v) for k,v in dsns.items()}
    role_sql="""
    SELECT session_user,current_user,current_setting('is_superuser', true) AS is_superuser,
           current_database() AS database, current_schema() AS schema,
           (SELECT array_agg(r.rolname ORDER BY r.rolname) FROM pg_roles r JOIN pg_auth_members m ON m.roleid=r.oid WHERE m.member=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS member_of
    """
    report['roles']={k: pg_probe(k,v,role_sql) for k,v in dsns.items() if v}
    catalog_sql="""
    WITH objs AS (
      SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) owner,c.relacl,
             obj_description(c.oid,'pg_class') descr
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE (n.nspname,c.relname) IN (('epidemic_p1_12','discovery_requests_v2'),('epidemic_p1_12','discovery_queries_v2'),('epidemic_p1_12','candidate_extraction_runs'),('epidemic_p1_12','candidate_preview_rows_v2'),('epidemic_p1_12','candidate_eligibility_v2'),('epidemic_discovery','source_candidates'),('epidemic_discovery','source_candidate_versions'),('epidemic_discovery','source_review_cases'),('epidemic_discovery','source_preview_rows'),('epidemic_discovery','candidate_fetches'),('epidemic_discovery','source_discovery_search_results'))
    ) SELECT * FROM objs ORDER BY nspname,relname
    """
    report['catalog_objects_app']=pg_probe('app', dsns.get('app'), catalog_sql) if dsns.get('app') else {}
    func_sql="""
    SELECT n.nspname schema,p.oid::regprocedure::text identity_signature,pg_get_userbyid(p.proowner) owner,l.lanname language,
           p.provolatile volatility,p.prosecdef security_definer,p.proleakproof leakproof,p.proacl acl,p.proconfig,
           pg_get_functiondef(p.oid) functiondef
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
    WHERE n.nspname='epidemic_p1_12' AND p.proname IN ('review_ready_v2_violations','mark_review_ready_v2','review_ready_v2_guard')
    ORDER BY identity_signature
    """
    report['gate_functions']=pg_probe('app', dsns.get('app'), func_sql) if dsns.get('app') else {}
    dep_sql="""
    SELECT cl.relkind, ns.nspname AS schema, cl.relname AS name
    FROM pg_depend d
    JOIN pg_proc p ON p.oid=d.objid
    JOIN pg_rewrite rw ON rw.oid=d.refobjid
    JOIN pg_class cl ON cl.oid=rw.ev_class
    JOIN pg_namespace ns ON ns.oid=cl.relnamespace
    WHERE p.oid='epidemic_p1_12.review_ready_v2_violations(uuid)'::regprocedure
    UNION
    SELECT cl.relkind, ns.nspname, cl.relname
    FROM pg_depend d
    JOIN pg_proc p ON p.oid=d.objid
    JOIN pg_class cl ON cl.oid=d.refobjid
    JOIN pg_namespace ns ON ns.oid=cl.relnamespace
    WHERE p.oid='epidemic_p1_12.review_ready_v2_violations(uuid)'::regprocedure
    ORDER BY schema,name
    """
    report['gate_dependencies']=pg_probe('app', dsns.get('app'), dep_sql) if dsns.get('app') else {}
    trig_sql="""
    SELECT event_object_schema,event_object_table,trigger_name,action_timing,event_manipulation,action_statement
    FROM information_schema.triggers WHERE trigger_name LIKE '%review_ready_v2%' ORDER BY 1,2,3
    """
    report['gate_triggers']=pg_probe('app', dsns.get('app'), trig_sql) if dsns.get('app') else {}
    counts_sql="""
    SELECT 'discovery_runs_p114b' name,count(*)::bigint n FROM epidemic_discovery.discovery_runs WHERE request_json::text LIKE '%p1-14b%'
    UNION ALL SELECT 'search_results_p114b',count(*) FROM epidemic_discovery.source_discovery_search_results WHERE provider_id='cdc-stacks-direct' OR metadata::text LIKE '%p1-14b%'
    UNION ALL SELECT 'fetches_p114b',count(*) FROM epidemic_discovery.candidate_fetches WHERE artifact_sha256 IN ('ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc') OR domain='stacks.cdc.gov'
    UNION ALL SELECT 'west_africa_candidates',count(*) FROM epidemic_discovery.source_candidates WHERE dataset_name ILIKE '%West Africa%' OR dataset_name ILIKE '%Guinea%' OR dataset_name ILIKE '%Liberia%' OR dataset_name ILIKE '%Sierra Leone%'
    UNION ALL SELECT 'west_africa_versions',count(*) FROM epidemic_discovery.source_candidate_versions WHERE content_sha256 IN ('ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc')
    UNION ALL SELECT 'west_africa_review_cases',count(*) FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.content_sha256 IN ('ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc')
    UNION ALL SELECT 'west_africa_preview_v2',count(*) FROM epidemic_p1_12.candidate_preview_rows_v2 WHERE artifact_sha256 IN ('ca263d3fae5764dc76bc8acd647674d4d2d427c0e9f5526fc484ca1388e8fc32','3b66347801c780d65c2d2d1c8180abae683c2d8b0a0a74f5db6c989f7cdca4ee','9c86ca4827173a82bcb187dd249749f31aab10eb96fcedfb103a30180620e8dc')
    UNION ALL SELECT 'review_decisions',count(*) FROM epidemic_discovery.source_review_decisions
    UNION ALL SELECT 'promotions',count(*) FROM epidemic_discovery.source_promotion_runs
    UNION ALL SELECT 'trusted_ebola',count(*) FROM epidemic_core.observations WHERE disease_id='ebola-disease'
    """
    report['counts_app']=pg_probe('app', dsns.get('app'), counts_sql) if dsns.get('app') else {}
    reproduce_sql="""
    BEGIN;
    SET LOCAL statement_timeout='30s';
    SELECT epidemic_p1_12.review_ready_v2_violations('00000000-0000-0000-0000-000000000000'::uuid) AS violations;
    ROLLBACK;
    """
    report['reproduce_gate_call_app']=pg_probe('app', dsns.get('app'), reproduce_sql) if dsns.get('app') else {}
    priv_sql="""
    SELECT 'schema_usage' item, has_schema_privilege(current_user,'epidemic_p1_12','USAGE')::text val
    UNION ALL SELECT 'violations_execute', has_function_privilege(current_user,'epidemic_p1_12.review_ready_v2_violations(uuid)','EXECUTE')::text
    UNION ALL SELECT 'mark_execute', has_function_privilege(current_user,'epidemic_p1_12.mark_review_ready_v2(uuid,character)','EXECUTE')::text
    UNION ALL SELECT 'source_review_cases_insert', has_table_privilege(current_user,'epidemic_discovery.source_review_cases','INSERT')::text
    UNION ALL SELECT 'preview_rows_insert', has_table_privilege(current_user,'epidemic_p1_12.candidate_preview_rows_v2','INSERT')::text
    UNION ALL SELECT 'extraction_insert', has_table_privilege(current_user,'epidemic_p1_12.candidate_extraction_runs','INSERT')::text
    """
    report['privileges_app']=pg_probe('app', dsns.get('app'), priv_sql) if dsns.get('app') else {}
    code_search = run("rg -n 'materialize_verified_candidate|review_ready_v2_violations|mark_review_ready_v2|candidate_preview_rows_v2|discovery_requests_v2|candidate_extraction_runs|source_review_cases' src migrations tests -g '*.py'", PROJECT, 120)
    report['code_search']=code_search
    report['diagnosis_classification']=['MISSING_EXACT_FUNCTION_EXECUTE','V2_EVIDENCE_PRODUCER_MISSING']
    report['completed_before_changes']=True
except Exception as exc:
    report['fatal']={'type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()[-4000:]}
finally:
    (OUT/'diagnosis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding='utf-8')
    md=[]
    md.append('# P1-14C v2 Preview Path Diagnosis')
    md.append(f"Generated: {report.get('generated_at_utc')}")
    md.append(f"Completed before changes: {report.get('completed_before_changes')}")
    git=report.get('git',{})
    md.append(f"HEAD: {(git.get('head') or {}).get('stdout','').strip()}")
    md.append(f"Branch: {(git.get('branch') or {}).get('stdout','').strip()}")
    md.append('Root cause categories: '+', '.join(report.get('diagnosis_classification',[])))
    md.append('Protected hashes:')
    for k,v in report.get('protected_hashes',{}).items(): md.append(f"- {k}: {v}")
    md.append('Privileges/app:')
    rows=((report.get('privileges_app') or {}).get('rows') or [])
    for r in rows: md.append(f"- {r.get('item')}: {r.get('val')}")
    md.append('Counts/app:')
    for r in ((report.get('counts_app') or {}).get('rows') or []): md.append(f"- {r.get('name')}: {r.get('n')}")
    if report.get('fatal'): md.append('Fatal: '+json.dumps(report['fatal'], ensure_ascii=False))
    (OUT/'diagnosis.md').write_text('\n'.join(md)+'\n', encoding='utf-8')
    print(json.dumps({'diagnosis_written': str(OUT), 'completed_before_changes': report.get('completed_before_changes'), 'root_causes': report.get('diagnosis_classification'), 'fatal': report.get('fatal') is not None}, ensure_ascii=False))
'''

def main():
    client, gate = helper.p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = helper.p111.p110r3.previous.p19.a100.paths(client)
        remote_dir = '/tmp/p1_14c_v2_preview_path'
        remote_script = remote_dir + '/diagnose.py'
        helper.p111.p110r3.previous.p19.a100.checked(client, f"mkdir -p {helper.q(remote_dir)}")
        with client.open_sftp() as sftp:
            with sftp.open(remote_script, 'w') as f:
                f.write(REMOTE)
        rc, out, err = helper.p111.p110r3.previous.p19.a100.run(
            client,
            f"P1_14C_PROJECT={helper.q(project)} python3 {helper.q(remote_script)}",
            timeout=600,
        )
        print(out)
        if err:
            print(err, file=sys.stderr)
        if rc != 0:
            raise SystemExit(rc)
    finally:
        client.close()
if __name__ == '__main__':
    main()

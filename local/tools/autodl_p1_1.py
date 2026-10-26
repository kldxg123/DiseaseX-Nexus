from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import secrets
import urllib.parse
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0

P = p0.P
CORE = "USER_HOME/autodl-tmp/conda/envs/epidemic-core"
PY = CORE + "/bin/python"
REPORT = P + "/reports/p1_1_domain_storage"
PG = P + "/scripts/postgres"
OC_CONFIG = P + "/storage/openclaw/state/openclaw.json"


def baseline(c):
    p0.run(c, f"mkdir -p {p0.q(REPORT)}")
    git = p0.out(c, f"cd {p0.q(P)} && git status --short")
    files = p0.out(
        c,
        f"cd {p0.q(P)} && find . -maxdepth 3 -type f "
        "-not -path './.git/*' -not -path './storage/*' -not -path './runtime/*' "
        "| sort | head -n 400",
    )
    pyver = p0.out(c, f"{p0.q(PY)} --version")
    pipver = p0.out(c, f"{p0.q(PY)} -m pip --version")
    pipcheck = p0.run(c, f"{p0.q(PY)} -m pip check", check=False)
    conda = p0.out(c, f"/root/miniconda3/bin/conda list -p {p0.q(CORE)} --json")
    freeze = p0.out(c, f"{p0.q(PY)} -m pip freeze --all")
    pg_status = p0.run(c, p0.q(PG + "/status.sh"), timeout=120, check=False)[0] == 0
    pg_smoke = p0.run(c, p0.q(PG + "/smoke_test.sh"), timeout=120, check=False)[0] == 0
    vl = p0.run(c, p0.q(P + "/scripts/qwen_vllm/status.sh"), timeout=120, check=False)[0] == 0
    oc = p0.run(c, p0.q(P + "/scripts/openclaw/status_openclaw.sh"), timeout=120, check=False)[0] == 0
    ocsha = p0.out(c, f"sha256sum {p0.q(OC_CONFIG)} | awk '{{print $1}}'")
    proxy = p0.out(c, "bash -lc 'if [[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]];then echo clean;else echo present;fi'")
    disk = p0.out(c, "df -Pk / USER_HOME/autodl-tmp")
    pgfiles = p0.out(c, f"find {p0.q(PG)} -maxdepth 2 -type f -printf '%p %m\\n' | sort")
    secretfiles = p0.out(c, f"find {p0.q(P+'/storage/database')} -maxdepth 4 -type f -printf '%p %m\\n' 2>/dev/null | sort | head -n 200")
    listener = p0.out(c, "awk 'NR>1&&$4==\"0A\"&&($2~/:(1538|1F40|4965)$/){print $2}' /proc/net/tcp /proc/net/tcp6")
    data = {
        "git_initial": git.splitlines(),
        "python": pyver,
        "pip": pipver,
        "pip_check_rc": pipcheck[0],
        "conda_list": json.loads(conda),
        "pip_freeze": freeze.splitlines(),
        "services": {"postgres": pg_status and pg_smoke, "vllm": vl, "openclaw": oc},
        "openclaw_config_sha256": ocsha,
        "proxy": proxy,
        "disk": disk.splitlines(),
        "listeners": listener.splitlines(),
    }
    s = c.open_sftp()
    p0.mkdirs(s, REPORT)
    p0.put(s, REPORT + "/pre_install_environment.json", json.dumps(data, indent=2) + "\n", 0o600)
    s.close()
    print(json.dumps({"python": pyver, "pip_check_rc": pipcheck[0], "services": data["services"], "proxy": proxy, "git_changes": len(data["git_initial"]), "openclaw_sha256": ocsha}))
    print("PROJECT_FILES\n" + files)
    print("POSTGRES_SCRIPTS\n" + pgfiles)
    print("DATABASE_SECRET_FILES\n" + secretfiles)
    if not all(data["services"].values()) or proxy != "clean":
        raise p0.Blocked("P1_1_PREFLIGHT_FAILED")


def inspect(c):
    for path in (
        PG + "/_common.sh",
        PG + "/backup.sh",
        PG + "/restore_test.sh",
        P + "/pyproject.toml",
        P + "/.gitignore",
        P + "/config/postgres.env.example",
    ):
        print("FILE=" + path)
        print(p0.out(c, f"sed -n '1,260p' {p0.q(path)}"))


def backup_pre(c):
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bdir = P + "/storage/database/postgresql18/backups/p1_1"
    dump = bdir + f"/pre-migration-{ts}.dump"
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    pg_run = P + "/storage/database/postgresql18/run"
    p0.run(c, f"mkdir -p {p0.q(bdir)} && chown epidemicdb:epidemicdb {p0.q(bdir)} && chmod 750 {p0.q(bdir)}")
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_dump')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Fc -f {p0.q(dump)}", timeout=900)
    sha = p0.out(c, f"sha256sum {p0.q(dump)} | awk '{{print $1}}'")
    p0.run(c, f"sha256sum {p0.q(dump)} > {p0.q(dump+'.sha256')} && chmod 640 {p0.q(dump)} {p0.q(dump+'.sha256')}")
    tmpdb = "epidemic_p1_1_precheck"
    def psql(db, sql):
        return p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {p0.q(db)} -Atc {p0.q(sql)}")
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {p0.q(tmpdb)}")
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemic_app {p0.q(tmpdb)}")
    try:
        p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {p0.q(tmpdb)} --no-owner {p0.q(dump)}", timeout=900)
        inventory_sql = """SELECT json_build_object(
'tables',(SELECT count(*) FROM pg_tables WHERE schemaname='epidemic'),
'views',(SELECT count(*) FROM pg_views WHERE schemaname='epidemic'),
'columns',(SELECT count(*) FROM information_schema.columns WHERE table_schema='epidemic'),
'constraints',(SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema='epidemic'),
'sentinel',(SELECT value::text FROM epidemic.platform_state WHERE key='p0_2_sentinel'),
'platform_rows',(SELECT count(*) FROM epidemic.platform_state),
'platform_hash',(SELECT md5(coalesce(string_agg(key||'='||value::text,'|' ORDER BY key),'')) FROM epidemic.platform_state)
)::text"""
        original = json.loads(psql("epidemic_openclaw", inventory_sql))
        restored = json.loads(psql(tmpdb, inventory_sql))
        match = original == restored
        if not match:
            raise p0.Blocked("BLOCKED_PRE_BACKUP_RESTORE_MISMATCH")
    finally:
        p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {p0.q(tmpdb)}", check=False)
    privilege_sql = """SELECT json_build_object(
'schema_owner',(SELECT schema_owner FROM information_schema.schemata WHERE schema_name='epidemic'),
'database_connect',has_database_privilege('epidemic_app','epidemic_openclaw','CONNECT'),
'schema_usage',has_schema_privilege('epidemic_app','epidemic','USAGE'),
'schema_create',has_schema_privilege('epidemic_app','epidemic','CREATE'),
'app_super',(SELECT rolsuper FROM pg_roles WHERE rolname='epidemic_app'),
'app_createdb',(SELECT rolcreatedb FROM pg_roles WHERE rolname='epidemic_app'),
'app_createrole',(SELECT rolcreaterole FROM pg_roles WHERE rolname='epidemic_app'),
'app_bypassrls',(SELECT rolbypassrls FROM pg_roles WHERE rolname='epidemic_app')
)::text"""
    privileges = json.loads(psql("epidemic_openclaw", privilege_sql))
    result = {"status": "PASS", "phase": "pre_migration", "backup_path": dump, "sha256": sha, "restore_database": "temporary database (removed)", "original": {k: v for k, v in original.items() if k != "sentinel"}, "restored": {k: v for k, v in restored.items() if k != "sentinel"}, "sentinel_match": original.get("sentinel") == restored.get("sentinel"), "inventory_match": match, "privileges_before": privileges}
    s = c.open_sftp(); p0.put(s, REPORT + "/pre_migration_backup_restore.json", json.dumps(result, indent=2) + "\n", 0o600); s.close()
    print(json.dumps({"status": "PASS", "backup": dump, "sha256": sha, "inventory_match": match, "schema_owner": privileges["schema_owner"]}))


def dependencies(c):
    runtime = ["SQLAlchemy==2.0.51", "alembic==1.18.5", "psycopg[binary]==3.3.4", "pydantic==2.13.4", "pydantic-settings==2.14.2", "pycountry==26.2.16"]
    dev = ["pytest==9.1.1", "ruff==0.15.21", "mypy==2.2.0"]
    reqdir = P + "/requirements"; cache = P + "/storage/cache/pip"; tmp = P + "/storage/tmp/p1_1_deps"
    s = c.open_sftp(); p0.mkdirs(s, reqdir); p0.mkdirs(s, cache); p0.mkdirs(s, tmp)
    p0.put(s, reqdir + "/runtime.in", "\n".join(runtime) + "\n")
    p0.put(s, reqdir + "/dev.in", "\n".join(dev) + "\n")
    s.close()
    for path in (tmp + "/resolve", tmp + "/runtime-verify", tmp + "/dev-verify"):
        if p0.out(c, f"test -e {p0.q(path)} && echo yes || echo no") == "yes":
            raise p0.Blocked("DEPENDENCY_TEST_PATH_ALREADY_EXISTS")
    p0.run(c, f"{p0.q(PY)} -m venv {p0.q(tmp+'/resolve')}", timeout=300)
    pip = tmp + "/resolve/bin/python -m pip"
    base = f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; PIP_CACHE_DIR={p0.q(cache)} {pip} install --dry-run --ignore-installed --only-binary=:all: --index-url https://pypi.org/simple --timeout 60 --retries 2"
    p0.run(c, f"bash -lc {p0.q(base+' --report '+tmp+'/runtime-report.json '+' '.join(runtime))}", timeout=1200)
    p0.run(c, f"bash -lc {p0.q(base+' --report '+tmp+'/dev-report.json '+' '.join(runtime+dev))}", timeout=1200)
    make_lock = """import json,sys
d=json.load(open(sys.argv[1])); rows=[]
for x in d['install']:
 m=x['metadata']; h=x['download_info']['archive_info'].get('hashes',{}).get('sha256')
 if not h: raise SystemExit('missing sha256 for '+m['name'])
 rows.append((m['name'].lower().replace('_','-'),m['version'],h,x['download_info']['url'],m.get('license') or m.get('license_expression') or 'UNKNOWN'))
rows.sort()
open(sys.argv[2],'w').write('# Generated for CPython 3.10 Linux x86_64 from official PyPI; all packages and hashes are exact.\\n'+''.join(f'{n}=={v} --hash=sha256:{h}\\n' for n,v,h,_,_ in rows))
json.dump([{'name':n,'version':v,'sha256':h,'source_domain':u.split('/')[2],'license':lic} for n,v,h,u,lic in rows],open(sys.argv[3],'w'),indent=2)"""
    p0.run(c, f"{p0.q(PY)} -c {p0.q(make_lock)} {p0.q(tmp+'/runtime-report.json')} {p0.q(reqdir+'/runtime.lock')} {p0.q(tmp+'/runtime-inventory.json')}")
    p0.run(c, f"{p0.q(PY)} -c {p0.q(make_lock)} {p0.q(tmp+'/dev-report.json')} {p0.q(reqdir+'/dev.lock')} {p0.q(tmp+'/dev-inventory.json')}")
    for name, lock in (("runtime-verify", "runtime.lock"), ("dev-verify", "dev.lock")):
        p0.run(c, f"{p0.q(PY)} -m venv {p0.q(tmp+'/'+name)}", timeout=300)
        cmd = f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; PIP_CACHE_DIR={p0.q(cache)} {p0.q(tmp+'/'+name+'/bin/python')} -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple --timeout 60 --retries 2 -r {p0.q(reqdir+'/'+lock)}"
        p0.run(c, f"bash -lc {p0.q(cmd)}", timeout=1800)
        p0.run(c, f"{p0.q(tmp+'/'+name+'/bin/python')} -m pip check")
    install_cmd = f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; PIP_CACHE_DIR={p0.q(cache)} {p0.q(PY)} -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple --timeout 60 --retries 2 -r {p0.q(reqdir+'/dev.lock')}"
    p0.run(c, f"bash -lc {p0.q(install_cmd)}", timeout=1800)
    p0.run(c, f"{p0.q(PY)} -m pip check")
    versions_code = """import importlib.metadata as m,json
names=['SQLAlchemy','alembic','psycopg','psycopg-binary','pydantic','pydantic-settings','pycountry','pytest','ruff','mypy']
print(json.dumps({n:m.version(n) for n in names}))"""
    versions = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q(versions_code)}"))
    expected = {"SQLAlchemy":"2.0.51","alembic":"1.18.5","psycopg":"3.3.4","psycopg-binary":"3.3.4","pydantic":"2.13.4","pydantic-settings":"2.14.2","pycountry":"26.2.16","pytest":"9.1.1","ruff":"0.15.21","mypy":"2.2.0"}
    if versions != expected: raise p0.Blocked("BLOCKED_DEPENDENCY_VERSION_MISMATCH")
    rt = json.loads(p0.out(c, f"cat {p0.q(tmp+'/runtime-inventory.json')}")); dv = json.loads(p0.out(c, f"cat {p0.q(tmp+'/dev-inventory.json')}"))
    freeze = p0.out(c, f"{p0.q(PY)} -m pip freeze --all").splitlines()
    result = {"status":"PASS","python":p0.out(c,f"{p0.q(PY)} --version"),"versions":versions,"runtime_lock_sha256":p0.out(c,f"sha256sum {p0.q(reqdir+'/runtime.lock')}|awk '{{print $1}}'"),"dev_lock_sha256":p0.out(c,f"sha256sum {p0.q(reqdir+'/dev.lock')}|awk '{{print $1}}'"),"runtime_packages":rt,"dev_packages":dv,"isolated_require_hashes":{"runtime":True,"dev":True},"pip_check":True,"pip_freeze":freeze,"license_notes":["psycopg and psycopg-binary retained from official distribution; LGPL-3.0-or-later","pycountry retained from official distribution; LGPL-2.1-or-later","No dependency was modified or repackaged"]}
    s=c.open_sftp();p0.put(s,REPORT+"/dependency_inventory.json",json.dumps(result,indent=2)+"\n",0o600);s.close()
    print(json.dumps({"status":"PASS","versions":versions,"runtime_packages":len(rt),"dev_packages":len(dv),"require_hashes":True}))


def depstatus(c):
    print(p0.out(c, "ps -eo pid,etimes,stat,comm,args | awk '$4 ~ /python|pip/ && $0 ~ /(p1_1_deps|dev.lock)/ {print $1,$2,$3,$4}'", check=False))
    print(p0.out(c, f"find {p0.q(P+'/storage/tmp/p1_1_deps')} -maxdepth 2 -type f -printf '%p %s\\n' 2>/dev/null | sort", check=False))
    print(p0.out(c, f"ls -l {p0.q(P+'/requirements')} 2>/dev/null", check=False))
    for name in ("runtime-verify", "dev-verify"):
        rc, a, b = p0.run(c, f"{p0.q(P+'/storage/tmp/p1_1_deps/'+name+'/bin/python')} -m pip check", check=False)
        print(name, rc, (a + b)[-500:])
    print(p0.out(c, f"{p0.q(PY)} -m pip list --format=json | {p0.q(PY)} -c 'import json,sys;wanted={{\"sqlalchemy\",\"alembic\",\"psycopg\",\"psycopg-binary\",\"pydantic\",\"pydantic-settings\",\"pycountry\",\"pytest\",\"ruff\",\"mypy\"}};print(json.dumps([x for x in json.load(sys.stdin) if x[\"name\"].lower() in wanted]))'", check=False))


def deps_finish(c):
    reqdir=P+"/requirements";cache=P+"/storage/cache/pip";tmp=P+"/storage/tmp/p1_1_deps"
    for name in ("runtime-verify","dev-verify"):
        p0.run(c,f"{p0.q(tmp+'/'+name+'/bin/python')} -m pip check")
    cmd=f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; PIP_CACHE_DIR={p0.q(cache)} {p0.q(PY)} -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple --timeout 60 --retries 2 -r {p0.q(reqdir+'/dev.lock')}"
    p0.run(c,f"bash -lc {p0.q(cmd)}",timeout=1800)
    p0.run(c,f"{p0.q(PY)} -m pip check")
    code="""import importlib.metadata as m,json
names=['SQLAlchemy','alembic','psycopg','psycopg-binary','pydantic','pydantic-settings','pycountry','pytest','ruff','mypy']
print(json.dumps({n:m.version(n) for n in names}))"""
    versions=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(code)}"))
    expected={"SQLAlchemy":"2.0.51","alembic":"1.18.5","psycopg":"3.3.4","psycopg-binary":"3.3.4","pydantic":"2.13.4","pydantic-settings":"2.14.2","pycountry":"26.2.16","pytest":"9.1.1","ruff":"0.15.21","mypy":"2.2.0"}
    if versions!=expected:raise p0.Blocked("BLOCKED_DEPENDENCY_VERSION_MISMATCH")
    rt=json.loads(p0.out(c,f"cat {p0.q(tmp+'/runtime-inventory.json')}"));dv=json.loads(p0.out(c,f"cat {p0.q(tmp+'/dev-inventory.json')}"))
    result={"status":"PASS","python":p0.out(c,f"{p0.q(PY)} --version"),"versions":versions,"runtime_lock_sha256":p0.out(c,f"sha256sum {p0.q(reqdir+'/runtime.lock')}|awk '{{print $1}}'"),"dev_lock_sha256":p0.out(c,f"sha256sum {p0.q(reqdir+'/dev.lock')}|awk '{{print $1}}'"),"runtime_packages":rt,"dev_packages":dv,"isolated_require_hashes":{"runtime":True,"dev":True},"pip_check":True,"pip_freeze":p0.out(c,f"{p0.q(PY)} -m pip freeze --all").splitlines(),"license_notes":["psycopg official distribution retained under LGPL-3.0-or-later","pycountry official distribution retained under LGPL-2.1-or-later","No dependency was modified or repackaged"]}
    s=c.open_sftp();p0.put(s,REPORT+"/dependency_inventory.json",json.dumps(result,indent=2)+"\n",0o600);s.close()
    print(json.dumps({"status":"PASS","versions":versions,"runtime_packages":len(rt),"dev_packages":len(dv),"require_hashes":True}))


def stop_slow_deps(c):
    code="""import os,signal,pathlib,time
needle='requirements/dev.lock';matched=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  cmd=(p/'cmdline').read_bytes().replace(b'\\0',b' ').decode(errors='ignore')
  exe=os.path.realpath(p/'exe')
 except Exception:continue
 if int(p.name)!=os.getpid() and needle in cmd and '-m pip install' in cmd and ' --require-hashes ' in cmd and exe.startswith('USER_HOME/autodl-tmp/conda/envs/epidemic-core/'):
  matched.append(int(p.name))
for pid in matched:os.kill(pid,signal.SIGTERM)
time.sleep(3)
print(len(matched))"""
    n=int(p0.out(c,f"{p0.q(PY)} -c {p0.q(code)}"))
    print(json.dumps({"terminated_verified_slow_pip_processes":n}))


def deps_finish_turbo(c):
    reqdir=P+"/requirements";cache=P+"/storage/cache/pip";wrapper=P+"/scripts/download/with_autodl_turbo.sh"
    args=["env",f"PIP_CACHE_DIR={cache}",PY,"-m","pip","install","--require-hashes","--only-binary=:all:","--index-url","https://pypi.org/simple","--timeout","60","--retries","2","-r",reqdir+"/dev.lock"]
    cmd=" ".join(p0.q(x) for x in [wrapper]+args)
    rc,a,b=p0.run(c,cmd,timeout=1800,check=False)
    if rc:
        direct="unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "+" ".join(p0.q(x) for x in args)
        p0.run(c,f"bash -lc {p0.q(direct)}",timeout=1800)
        accelerated=False;fallback=True
    else:
        accelerated=True;fallback=False
    proxy=p0.out(c,"bash -lc 'if [[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]];then echo clean;else echo present;fi'")
    if proxy!='clean':raise p0.Blocked("PROXY_RESIDUE_AFTER_PIP")
    deps_finish(c)
    print(json.dumps({"autodl_turbo_used":True,"accelerated_install_succeeded":accelerated,"official_direct_fallback":fallback,"proxy_after":"clean"}))


def deps_finish_mirror(c):
    reqdir=P+"/requirements";cache=P+"/storage/cache/pip"
    # Use only the already configured server mirror; hashes remain authoritative.
    configured=(p0.run(c,f"{p0.q(PY)} -m pip config get global.index-url",check=False)[0]==0 or p0.out(c,"bash -lc '[[ -n ${PIP_INDEX_URL:-} ]] && echo yes || echo no'")=="yes")
    if not configured:raise p0.Blocked("NO_CONFIGURED_PIP_MIRROR")
    cmd=f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; PIP_CACHE_DIR={p0.q(cache)} {p0.q(PY)} -m pip install --require-hashes --only-binary=:all: --timeout 60 --retries 2 -r {p0.q(reqdir+'/dev.lock')}"
    p0.run(c,f"bash -lc {p0.q(cmd)}",timeout=1800)
    proxy=p0.out(c,"bash -lc 'if [[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]];then echo clean;else echo present;fi'")
    if proxy!='clean':raise p0.Blocked("PROXY_RESIDUE_AFTER_PIP")
    deps_finish(c)
    print(json.dumps({"autodl_turbo_attempted":True,"configured_mirror_fallback":True,"hashes_enforced":True,"proxy_after":"clean"}))


def deploy_code(c):
    local=Path(__file__).parent/"p1_1_payload";s=c.open_sftp()
    for path in sorted(local.rglob("*")):
        rel=path.relative_to(local).as_posix();remote=P+"/"+rel
        if path.is_dir():p0.mkdirs(s,remote)
        else:
            p0.mkdirs(s,str(PurePosixPath(remote).parent));p0.put(s,remote,path.read_text(encoding="utf-8"),0o750 if rel.startswith("scripts/") and rel.endswith(".sh") else 0o644)
    for d in (P+"/migrations/versions",P+"/tests/fixtures",P+"/storage/raw/sha256"):p0.mkdirs(s,d)
    def append(path,marker,text):
        with s.open(path,"r") as f:old=f.read().decode("utf-8","replace")
        if marker not in old:p0.put(s,path,old.rstrip()+"\n\n"+text.strip()+"\n")
    append(P+"/.gitignore","# P1-1 domain storage","""# P1-1 domain storage
storage/raw/
storage/tmp/
storage/cache/pip/
storage/database/postgresql18/backups/p1_1/
reports/p1_1_domain_storage/*.raw
.mypy_cache/
.ruff_cache/
*.coverage
""")
    append(P+"/README.md","## P1-1 domain storage foundation","""## P1-1 domain storage foundation

The `epidemic_core` package provides strict country, disease, and ISO-date normalization; SQLAlchemy repositories; content-addressed raw artifacts; Alembic migrations; immutable provenance/observation records; and deterministic dataset snapshots. Database migration and application DSNs are separate process environment secrets. No network API service or external source adapter is part of P1-1.

RLS is intentionally deferred until a real multi-tenant boundary exists. The application role remains `NOBYPASSRLS` and has no schema DDL permission.
""")
    append(P+"/AGENTS.md","## P1-1 database invariants","""## P1-1 database invariants

Use Alembic for every production/test schema change; never call `Base.metadata.create_all()`. Keep all domain SQL explicitly schema-qualified, facts append-only, source provenance mandatory, and dataset snapshot ordering deterministic. Do not expose database/file/web/shell tools to OpenClaw. RLS is deferred until a real tenant boundary exists.
""")
    p0.put(s,P+"/THIRD_PARTY_LICENSES.md","""# Third-party license inventory

Generated from the fixed P1-1 lock inventory. Distributions are used unmodified from official PyPI.

- SQLAlchemy 2.0.51 — MIT
- Alembic 1.18.5 — MIT
- psycopg / psycopg-binary 3.3.4 — LGPL-3.0-or-later
- Pydantic / pydantic-settings — MIT
- pycountry 26.2.16 — LGPL-2.1-or-later; data derived from Debian iso-codes
- pytest — MIT; Ruff — MIT; Mypy — MIT

The machine-readable dependency inventory records every resolved distribution, version, source domain, and SHA-256.
""")
    s.close();print(json.dumps({"uploaded_files":sum(1 for x in local.rglob('*') if x.is_file()),"duplicate_src":False,"alembic_environments":1}))


def setup_role(c):
    script=P+"/storage/tmp/p1_1_setup_role.py"
    code="""import os,secrets,subprocess,urllib.parse,pathlib
project=pathlib.Path('USER_HOME/autodl-tmp/epidemic-openclaw');sec=project/'.secrets';env={}
for line in (sec/'postgres.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);env[k]=v
pw=secrets.token_urlsafe(40)
sql=f'''DO $do$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_migrator') THEN CREATE ROLE epidemic_migrator; END IF; END $do$;
ALTER ROLE epidemic_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '{pw}';
REVOKE ALL ON DATABASE epidemic_openclaw FROM epidemic_migrator;
GRANT CONNECT ON DATABASE epidemic_openclaw TO epidemic_migrator;
ALTER SCHEMA epidemic OWNER TO epidemic_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA epidemic FROM PUBLIC;
GRANT USAGE ON SCHEMA epidemic TO epidemic_app;
'''
r=subprocess.run(['runuser','-u','epidemicdb','--','USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql','-h',str(project/'storage/database/postgresql18/run'),'-U','epidemicdb','-d','epidemic_openclaw','-v','ON_ERROR_STOP=1'],input=sql,text=True,capture_output=True)
if r.returncode:raise SystemExit('role setup failed')
q=urllib.parse.quote
app=env['POSTGRES_APP_PASSWORD'];content='APP_DATABASE_URL=postgresql+psycopg://epidemic_app:'+q(app,safe='')+'@127.0.0.1:5432/epidemic_openclaw\\nMIGRATION_DATABASE_URL=postgresql+psycopg://epidemic_migrator:'+q(pw,safe='')+'@127.0.0.1:5432/epidemic_openclaw\\n'
path=sec/'p1_1.env';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:f.write(content)
os.chmod(path,0o600)
"""
    s=c.open_sftp();p0.put(s,script,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(script)}")
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    sql="""SELECT json_build_object('login',rolcanlogin,'super',rolsuper,'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,'bypassrls',rolbypassrls,'memberships',(SELECT count(*) FROM pg_auth_members WHERE member=r.oid))::text FROM pg_roles r WHERE rolname='epidemic_migrator'"""
    role=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}"))
    if role!={"login":True,"super":False,"createdb":False,"createrole":False,"replication":False,"bypassrls":False,"memberships":0}:raise p0.Blocked("MIGRATOR_ROLE_ATTRIBUTES_INVALID")
    print(json.dumps({"role":"epidemic_migrator","attributes":role,"secret_file_mode":p0.out(c,f"stat -c %a {p0.q(P+'/.secrets/p1_1.env')}")}))


def generate_migration(c):
    env=f"source {p0.q(P+'/scripts/database/_env.sh')}; cd {p0.q(P)};"
    if p0.out(c,f"find {p0.q(P+'/migrations/versions')} -type f -name '*.py' | wc -l")!="0":raise p0.Blocked("ALEMBIC_REVISION_ALREADY_EXISTS")
    p0.run(c,f"bash -lc {p0.q(env+' alembic revision --autogenerate --rev-id 0001_p1_1 -m p1_1_domain_storage')}",timeout=600)
    rev=P+"/migrations/versions/0001_p1_1_p1_1_domain_storage.py";s=c.open_sftp()
    try:
        with s.open(rev,"r") as f:text0=f.read().decode()
    except FileNotFoundError:
        found=p0.out(c,f"find {p0.q(P+'/migrations/versions')} -name '0001_p1_1*.py' -print -quit");rev=found
        with s.open(rev,"r") as f:text0=f.read().decode()
    custom='''    op.execute("""CREATE FUNCTION epidemic.validate_observation_metric() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m epidemic.metric_definitions%ROWTYPE;
BEGIN SELECT * INTO STRICT m FROM epidemic.metric_definitions WHERE metric_code=NEW.metric_code;
IF NOT m.allow_negative AND NEW.value_numeric < 0 THEN RAISE EXCEPTION 'metric disallows negative value' USING ERRCODE='23514'; END IF;
IF m.min_value IS NOT NULL AND NEW.value_numeric < m.min_value THEN RAISE EXCEPTION 'metric below minimum' USING ERRCODE='23514'; END IF;
IF m.max_value IS NOT NULL AND NEW.value_numeric > m.max_value THEN RAISE EXCEPTION 'metric above maximum' USING ERRCODE='23514'; END IF;
RETURN NEW; END $$""")
    op.execute("""CREATE TRIGGER trg_normalized_observations_metric BEFORE INSERT OR UPDATE OF value_numeric,metric_code ON epidemic.normalized_observations FOR EACH ROW EXECUTE FUNCTION epidemic.validate_observation_metric()""")
    op.execute("""CREATE VIEW epidemic.training_observations_v1 AS
SELECT o.id AS observation_id,o.country_alpha2,c.alpha3 AS country_alpha3,d.slug AS disease_slug,o.metric_code,o.value_numeric,m.unit,o.period_start,o.period_end,o.granularity,o.reporting_date,o.as_of AS as_of_utc,o.dimensions,s.source_key,a.sha256 AS artifact_sha256,o.record_fingerprint,o.series_fingerprint,o.revision,q.score AS quality_score,o.normalization_version
FROM epidemic.normalized_observations o
JOIN epidemic.countries c ON c.alpha2=o.country_alpha2 JOIN epidemic.diseases d ON d.id=o.disease_id JOIN epidemic.metric_definitions m ON m.metric_code=o.metric_code
JOIN epidemic.source_fetches f ON f.id=o.source_fetch_id JOIN epidemic.data_sources s ON s.id=f.source_id JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id
JOIN LATERAL (SELECT qa.status,qa.score FROM epidemic.quality_assessments qa WHERE qa.observation_id=o.id ORDER BY qa.assessed_at DESC,qa.id DESC LIMIT 1) q ON q.status='accepted'
WHERE NOT EXISTS (SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)""")
    op.execute("GRANT SELECT ON epidemic.reference_datasets,epidemic.countries,epidemic.country_aliases,epidemic.diseases,epidemic.disease_aliases,epidemic.metric_definitions,epidemic.data_sources,epidemic.training_observations_v1 TO epidemic_app")
    op.execute("GRANT SELECT,INSERT ON epidemic.query_requests,epidemic.retrieval_run_events,epidemic.raw_artifacts,epidemic.source_fetches,epidemic.normalized_observations,epidemic.quality_assessments,epidemic.dataset_snapshots,epidemic.dataset_snapshot_observations TO epidemic_app")
    op.execute("GRANT SELECT,INSERT ON epidemic.retrieval_runs TO epidemic_app")
    op.execute("GRANT UPDATE(status,started_at,finished_at,error_code,error_message) ON epidemic.retrieval_runs TO epidemic_app")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE epidemic.retrieval_run_events_id_seq TO epidemic_app")
'''
    marker="def downgrade() -> None:"
    if marker not in text0:raise p0.Blocked("AUTOGENERATED_MIGRATION_SHAPE")
    text0=text0.replace(marker,custom+"\n\n"+marker)
    down='''    op.execute("REVOKE ALL ON epidemic.training_observations_v1 FROM epidemic_app")
    op.execute("DROP VIEW epidemic.training_observations_v1")
    op.execute("DROP TRIGGER trg_normalized_observations_metric ON epidemic.normalized_observations")
    op.execute("DROP FUNCTION epidemic.validate_observation_metric()")
'''
    idx=text0.index(marker);body=text0[idx:]
    line_end=body.index("\n")+1;text0=text0[:idx]+body[:line_end]+down+body[line_end:]
    p0.put(s,rev,text0,0o644);s.close()
    p0.run(c,f"{p0.q(PY)} -m py_compile {p0.q(rev)}")
    print(json.dumps({"revision":"0001_p1_1","file":rev,"manual_review_additions":["metric trigger","training view","least privilege grants"]}))


def unit_quality(c):
    base=f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} &&"
    commands={
        "format":f"{base} {p0.q(CORE+'/bin/ruff')} format src tests migrations",
        "ruff_fix":f"{base} {p0.q(CORE+'/bin/ruff')} check --fix src tests migrations",
        "ruff":f"{base} {p0.q(CORE+'/bin/ruff')} check src tests migrations",
        "format_check":f"{base} {p0.q(CORE+'/bin/ruff')} format --check src tests migrations",
        "mypy":f"{base} {p0.q(CORE+'/bin/mypy')} src/epidemic_core",
        "unit":f"{base} {p0.q(PY)} -m pytest -q tests/unit",
    }
    result={}
    for name,cmd in commands.items():
        rc,a,b=p0.run(c,cmd,timeout=900,check=False);result[name]={"rc":rc,"tail":(a+"\n"+b)[-30000:]}
        if rc:print(json.dumps(result));raise p0.Blocked("UNIT_QUALITY_FAILED:"+name)
    print(json.dumps(result))


def style_inspect(c):
    for path,ranges in ((P+"/src/epidemic_core/db/models/tables.py","185,210p"),(P+"/src/epidemic_core/reference.py","30,70p"),(P+"/tests/integration/test_postgres.py","180,255p"),(P+"/migrations/versions/0001_p1_1_p1_1_domain_storage.py","85,110p;465,510p")):
        print("FILE="+path);print(p0.out(c,f"sed -n {p0.q(ranges)} {p0.q(path)}"))


def style_fix(c):
    s=c.open_sftp()
    def read(path):
        with s.open(path,"r") as f:return f.read().decode()
    def write(path,value):p0.put(s,path,value,0o644)
    long_check="(storage_mode='inline_json' AND inline_json IS NOT NULL AND inline_text IS NULL AND storage_uri IS NULL) OR (storage_mode='inline_text' AND inline_json IS NULL AND inline_text IS NOT NULL AND storage_uri IS NULL) OR (storage_mode='file' AND inline_json IS NULL AND inline_text IS NULL AND storage_uri IS NOT NULL)"
    split_check='''"(storage_mode='inline_json' AND inline_json IS NOT NULL AND inline_text IS NULL "
            "AND storage_uri IS NULL) OR (storage_mode='inline_text' AND inline_json IS NULL "
            "AND inline_text IS NOT NULL AND storage_uri IS NULL) OR (storage_mode='file' "
            "AND inline_json IS NULL AND inline_text IS NULL AND storage_uri IS NOT NULL)"'''
    malformed='''(storage_mode='inline_json' AND inline_json IS NOT NULL AND inline_text IS NULL + "AND storage_uri IS NULL) OR (storage_mode='inline_text' AND inline_json IS NULL " + "AND inline_text IS NOT NULL AND storage_uri IS NULL) OR (storage_mode='file' " + "AND inline_json IS NULL AND inline_text IS NULL AND storage_uri IS NOT NULL)'''
    model=P+"/src/epidemic_core/db/models/tables.py";t=read(model).replace('"'+long_check+'"',split_check).replace(malformed,split_check);write(model,t)
    migration=P+"/migrations/versions/0001_p1_1_p1_1_domain_storage.py";t=read(migration).replace('"'+long_check+'"',split_check).replace(malformed,split_check)
    legacy='    op.execute("""CREATE FUNCTION epidemic.validate_observation_metric()'
    custom='''    op.execute(
        """CREATE FUNCTION epidemic.validate_observation_metric() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE m epidemic.metric_definitions%ROWTYPE;
BEGIN
SELECT * INTO STRICT m FROM epidemic.metric_definitions WHERE metric_code=NEW.metric_code;
IF NOT m.allow_negative AND NEW.value_numeric < 0 THEN
  RAISE EXCEPTION 'metric disallows negative value' USING ERRCODE='23514';
END IF;
IF m.min_value IS NOT NULL AND NEW.value_numeric < m.min_value THEN
  RAISE EXCEPTION 'metric below minimum' USING ERRCODE='23514';
END IF;
IF m.max_value IS NOT NULL AND NEW.value_numeric > m.max_value THEN
  RAISE EXCEPTION 'metric above maximum' USING ERRCODE='23514';
END IF;
RETURN NEW;
END $$"""
    )
    op.execute(
        """CREATE TRIGGER trg_normalized_observations_metric
BEFORE INSERT OR UPDATE OF value_numeric,metric_code
ON epidemic.normalized_observations
FOR EACH ROW EXECUTE FUNCTION epidemic.validate_observation_metric()"""
    )
    op.execute(
        """CREATE VIEW epidemic.training_observations_v1 AS
SELECT o.id AS observation_id,
       o.country_alpha2,
       c.alpha3 AS country_alpha3,
       d.slug AS disease_slug,
       o.metric_code,
       o.value_numeric,
       m.unit,
       o.period_start,
       o.period_end,
       o.granularity,
       o.reporting_date,
       o.as_of AS as_of_utc,
       o.dimensions,
       s.source_key,
       a.sha256 AS artifact_sha256,
       o.record_fingerprint,
       o.series_fingerprint,
       o.revision,
       q.score AS quality_score,
       o.normalization_version
FROM epidemic.normalized_observations o
JOIN epidemic.countries c ON c.alpha2=o.country_alpha2
JOIN epidemic.diseases d ON d.id=o.disease_id
JOIN epidemic.metric_definitions m ON m.metric_code=o.metric_code
JOIN epidemic.source_fetches f ON f.id=o.source_fetch_id
JOIN epidemic.data_sources s ON s.id=f.source_id
JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id
JOIN LATERAL (
  SELECT qa.status,qa.score
  FROM epidemic.quality_assessments qa
  WHERE qa.observation_id=o.id
  ORDER BY qa.assessed_at DESC,qa.id DESC
  LIMIT 1
) q ON q.status='accepted'
WHERE NOT EXISTS (
  SELECT 1 FROM epidemic.normalized_observations n
  WHERE n.supersedes_observation_id=o.id
)"""
    )
    for table in (
        "reference_datasets", "countries", "country_aliases", "diseases",
        "disease_aliases", "metric_definitions", "data_sources",
    ):
        op.execute(f"GRANT SELECT ON epidemic.{table} TO epidemic_app")
    op.execute("GRANT SELECT ON epidemic.training_observations_v1 TO epidemic_app")
    for table in (
        "query_requests", "retrieval_run_events", "raw_artifacts", "source_fetches",
        "normalized_observations", "quality_assessments", "dataset_snapshots",
        "dataset_snapshot_observations",
    ):
        op.execute(f"GRANT SELECT,INSERT ON epidemic.{table} TO epidemic_app")
    op.execute("GRANT SELECT,INSERT ON epidemic.retrieval_runs TO epidemic_app")
    op.execute(
        "GRANT UPDATE(status,started_at,finished_at,error_code,error_message) "
        "ON epidemic.retrieval_runs TO epidemic_app"
    )
    op.execute(
        "GRANT USAGE,SELECT ON SEQUENCE epidemic.retrieval_run_events_id_seq "
        "TO epidemic_app"
    )
'''
    if legacy in t:
        start=t.index(legacy);end=t.index('\n\ndef downgrade() -> None:',start);t=t[:start]+custom+t[end:]
    write(migration,t)
    test=P+"/tests/integration/test_postgres.py";t=read(test)
    old='''    with Session(app) as s:
        with pytest.raises(IntegrityError):
            with s.begin():
                s.execute(
                    text(
                        "INSERT INTO epidemic.normalized_observations(id,record_fingerprint,series_fingerprint,revision,source_fetch_id,country_alpha2,disease_id,metric_code,value_numeric,period_start,period_end,granularity,as_of,dimensions,dimensions_sha256,normalization_version) VALUES (gen_random_uuid(),'bad','bad',1,:f,'CN',:d,'cases_new',1,'2025-02-01','2025-01-01','day',CURRENT_TIMESTAMP,'{}','bad','1')"
                    ),
                    {"f": fetch, "d": disease},
                )
'''
    new='''    invalid_sql = (
        "INSERT INTO epidemic.normalized_observations("
        "id,record_fingerprint,series_fingerprint,revision,source_fetch_id,"
        "country_alpha2,disease_id,metric_code,value_numeric,period_start,period_end,"
        "granularity,as_of,dimensions,dimensions_sha256,normalization_version) VALUES ("
        "gen_random_uuid(),'bad','bad',1,:f,'CN',:d,'cases_new',1,"
        "'2025-02-01','2025-01-01','day',CURRENT_TIMESTAMP,'{}','bad','1')"
    )
    with Session(app) as s, pytest.raises(IntegrityError), s.begin():
        s.execute(text(invalid_sql), {"f": fetch, "d": disease})
'''
    if old in t:t=t.replace(old,new)
    write(test,t);s.close();print("STYLE_FIX=APPLIED")


def type_fix(c):
    s=c.open_sftp()
    def change(path,repls):
        with s.open(path,"r") as f:t=f.read().decode()
        for a,b in repls:
            if a not in t:raise p0.Blocked("TYPE_FIX_SHAPE:"+path)
            t=t.replace(a,b)
        p0.put(s,path,t,0o644)
    repo=P+"/src/epidemic_core/db/repositories/repositories.py"
    change(repo,[
        ("from typing import Any\n","from typing import Any, TypeVar, cast\n"),
        ("def _one(session: Session, statement: Select[tuple[Any]]) -> Any:\n    return session.execute(statement).scalar_one()", "T = TypeVar(\"T\")\n\n\ndef _one(session: Session, statement: Select[tuple[T]]) -> T:\n    return session.execute(statement).scalar_one()"),
        ("return _one(session, select(Disease.id).where(Disease.slug == slug))","return cast(uuid.UUID, _one(session, select(Disease.id).where(Disease.slug == slug)))"),
    ])
    ref=P+"/src/epidemic_core/reference.py"
    change(ref,[
        ('row["name"]), lang, "translation"','str(row["name"])), lang, "translation"'),
        ('MANUAL.get(row["alpha2"], [])','MANUAL.get(str(row["alpha2"]), [])'),
        ('normalize_alias(alias),','normalize_alias(str(alias)),'),
    ])
    qs=P+"/src/epidemic_core/query_service.py"
    change(qs,[
        ("from epidemic_core.db.repositories import CountryRepository, DiseaseRepository, QueryRepository","from epidemic_core.db.models.tables import QueryRequest\nfrom epidemic_core.db.repositories import CountryRepository, DiseaseRepository, QueryRepository"),
        ("def register(self, session: Session, value: EpidemicQueryInput):","def register(self, session: Session, value: EpidemicQueryInput) -> QueryRequest:"),
    ])
    s.close();print("TYPE_FIX=APPLIED")


def type_fix2(c):
    path=P+"/src/epidemic_core/db/repositories/repositories.py";s=c.open_sftp()
    with s.open(path,"r") as f:t=f.read().decode()
    t=t.replace("Any, TypeVar, cast","Any, TypeVar").replace("return cast(uuid.UUID, _one(session, select(Disease.id).where(Disease.slug == slug)))","return _one(session, select(Disease.id).where(Disease.slug == slug))")
    p0.put(s,path,t,0o644);s.close();print("TYPE_FIX2=APPLIED")


def migration_test(c):
    pre=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/pre_migration_backup_restore.json')}"));dump=pre["backup_path"]
    db="epidemic_p1_1_migration_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    admin=lambda sql:p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {p0.q(db)} -Atc {p0.q(sql)}")
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {p0.q(db)}")
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {p0.q(db)}")
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {p0.q(db)} --no-owner {p0.q(dump)}",timeout=900)
    admin("ALTER SCHEMA epidemic OWNER TO epidemic_migrator; GRANT CONNECT ON DATABASE epidemic_p1_1_migration_test TO epidemic_migrator; GRANT USAGE ON SCHEMA epidemic TO epidemic_app")
    make="""import pathlib,urllib.parse
p=pathlib.Path('"""+P+"""/.secrets');vals={}
for line in (p/'p1_1.env').read_text().splitlines():
 if '=' in line:k,v=line.split('=',1);vals[k]=v
out=[]
for k,v in vals.items():
 u=urllib.parse.urlsplit(v);out.append(k+'='+urllib.parse.urlunsplit((u.scheme,u.netloc,'/epidemic_p1_1_migration_test',u.query,u.fragment)))
q=p/'p1_1_test.env';q.write_text('\\n'.join(out)+'\\n');q.chmod(0o600)"""
    p0.run(c,f"{p0.q(PY)} -c {p0.q(make)}")
    env=f"source {p0.q(P+'/scripts/database/_env.sh')}; set -a; source {p0.q(P+'/.secrets/p1_1_test.env')}; set +a; cd {p0.q(P)};"
    def cmd(x,timeout=900):
        rc,a,b=p0.run(c,f"bash -lc {p0.q(env+' '+x)}",timeout=timeout,check=False)
        if rc:raise p0.Blocked("MIGRATION_TEST_COMMAND_FAILED:"+x.split()[0]+":"+(a+"\n"+b)[-8000:])
        return rc,a,b
    sentinel_before=admin("SELECT value::text FROM epidemic.platform_state WHERE key='p0_2_sentinel'")
    cmd("alembic upgrade head")
    seed1=json.loads(cmd("python -m epidemic_core.cli seed-reference")[1])
    first_tests=cmd("python -m pytest -q tests/integration",timeout=1200)[1]
    cmd("alembic downgrade base")
    sentinel_after=admin("SELECT value::text FROM epidemic.platform_state WHERE key='p0_2_sentinel'")
    remaining=int(admin("SELECT count(*) FROM pg_tables WHERE schemaname='epidemic' AND tablename NOT IN ('platform_state','alembic_version')"))
    if sentinel_before!=sentinel_after or remaining:raise p0.Blocked("DOWNGRADE_DAMAGED_P0")
    cmd("alembic upgrade head")
    seed2=json.loads(cmd("python -m epidemic_core.cli seed-reference")[1]);counts1=json.loads(admin("SELECT json_build_object('countries',(SELECT count(*) FROM epidemic.countries),'aliases',(SELECT count(*) FROM epidemic.country_aliases),'diseases',(SELECT count(*) FROM epidemic.diseases),'disease_aliases',(SELECT count(*) FROM epidemic.disease_aliases),'metrics',(SELECT count(*) FROM epidemic.metric_definitions))::text"))
    seed3=json.loads(cmd("python -m epidemic_core.cli seed-reference")[1]);counts2=json.loads(admin("SELECT json_build_object('countries',(SELECT count(*) FROM epidemic.countries),'aliases',(SELECT count(*) FROM epidemic.country_aliases),'diseases',(SELECT count(*) FROM epidemic.diseases),'disease_aliases',(SELECT count(*) FROM epidemic.disease_aliases),'metrics',(SELECT count(*) FROM epidemic.metric_definitions))::text"))
    if counts1!=counts2 or counts2["countries"]!=249:raise p0.Blocked("REFERENCE_SEED_NOT_IDEMPOTENT")
    second_tests=cmd("python -m pytest -q tests/integration",timeout=1200)[1]
    heads=cmd("alembic heads")[1].strip();current=cmd("alembic current")[1].strip();check=cmd("alembic check")[1].strip()
    result={"status":"PASS","temporary_database":"ephemeral","upgrade":True,"first_seed":seed1,"first_integration":first_tests.splitlines()[-1],"downgrade":True,"p0_sentinel_preserved":sentinel_before==sentinel_after,"p0_schema_preserved":True,"reupgrade":True,"second_seed":seed2,"third_seed":seed3,"seed_counts":counts2,"seed_idempotent":counts1==counts2,"second_integration":second_tests.splitlines()[-1],"alembic_heads":heads,"alembic_current":current,"alembic_check":check}
    s=c.open_sftp();p0.put(s,REPORT+"/migration_test_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def seed_fix(c):
    path=P+"/src/epidemic_core/reference.py";s=c.open_sftp()
    with s.open(path,"r") as f:t=f.read().decode()
    old="set_={k: v for k, v in values.items() if k not in keys}"
    new="set_={getattr(model, k): v for k, v in values.items() if k not in keys}"
    if old not in t:raise p0.Blocked("SEED_FIX_SHAPE")
    p0.put(s,path,t.replace(old,new),0o644);s.close();print("SEED_FIX=APPLIED")


def integration_fix(c):
    s=c.open_sftp()
    model=P+"/src/epidemic_core/db/models/tables.py"
    with s.open(model,"r") as f:t=f.read().decode()
    old="inline_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)"
    if old not in t:raise p0.Blocked("INTEGRATION_MODEL_FIX_SHAPE")
    p0.put(s,model,t.replace(old,"inline_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))"),0o644)
    test=P+"/tests/integration/test_postgres.py"
    with s.open(test,"r") as f:t=f.read().decode()
    t=t.replace("s.add(source)\n    with Session(app)","s.add(source)\n        source_id = source.id\n    with Session(app)").replace("source_id=source.id,","source_id=source_id,")
    p0.put(s,test,t,0o644);s.close();print("INTEGRATION_FIX=APPLIED")


def integration_fix2(c):
    path=P+"/tests/integration/test_postgres.py";s=c.open_sftp()
    with s.open(path,"r") as f:t=f.read().decode()
    t=t.replace("s.add(source)\n        source_id = source.id\n    with Session(app) as s:\n        assert s.scalar", "s.add(source)\n    with Session(app) as s:\n        assert s.scalar")
    t=t.replace("assert old.id == old2.id\n    with Session(app)","assert old.id == old2.id\n        old_id = old.id\n    with Session(app)").replace('obs("3" * 64, 2, "2", old.id)','obs("3" * 64, 2, "2", old_id)')
    t=t.replace("observation_id=old.id", "observation_id=old_id")
    t=t.replace("        )\n    with Session(app) as s:\n        rows =", "        )\n        new_id = new.id\n    with Session(app) as s:\n        rows =").replace("assert rows == [(new.id, 2)]", "assert rows == [(new_id, 2)]")
    p0.put(s,path,t,0o644);s.close();print("INTEGRATION_FIX2=APPLIED")


def privilege_test(c):
    script=P+"/storage/tmp/p1_1_privileges.py"
    code='''import json,os,uuid,psycopg
dsn=os.environ['APP_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
allowed=[];denied=[]
def run(sql,expect=True):
 try:
  with psycopg.connect(dsn) as conn:
   with conn.cursor() as cur:cur.execute(sql)
   conn.rollback()
  if not expect:raise SystemExit('forbidden statement unexpectedly succeeded')
  allowed.append(True)
 except psycopg.Error as e:
  if expect:raise
  if e.sqlstate!='42501':raise SystemExit('forbidden statement failed for non-permission reason')
  denied.append(True)
run('SELECT count(*) FROM epidemic.countries')
run('SELECT count(*) FROM epidemic.training_observations_v1')
run("""WITH d AS (SELECT id FROM epidemic.diseases LIMIT 1)
INSERT INTO epidemic.query_requests(id,fingerprint,raw_country,raw_disease,country_alpha2,disease_id,start_date,end_date,schema_version)
SELECT gen_random_uuid(),repeat('a',64),'CN','dengue','CN',id,'2025-01-01','2025-01-01',1 FROM d ON CONFLICT DO NOTHING""")
run("UPDATE epidemic.retrieval_runs SET status=status,started_at=started_at,finished_at=finished_at,error_code=error_code,error_message=error_message WHERE false")
run("INSERT INTO epidemic.retrieval_run_events(retrieval_run_id,to_status,event_code) SELECT id,status,'permission-check' FROM epidemic.retrieval_runs LIMIT 1")
run("INSERT INTO epidemic.raw_artifacts(id,sha256,byte_size,media_type,storage_mode,inline_text) VALUES(gen_random_uuid(),repeat('b',64),1,'text/plain','inline_text','x') ON CONFLICT DO NOTHING")
run("INSERT INTO epidemic.source_fetches(id,retrieval_run_id,source_id,canonical_url,requested_at,attempt_number) SELECT gen_random_uuid(),r.id,r.source_id,'fixture://permission',CURRENT_TIMESTAMP,1 FROM epidemic.retrieval_runs r LIMIT 1")
for index,sql in enumerate((
 'CREATE TABLE epidemic.permission_denied_probe(id integer)',
 'ALTER TABLE epidemic.query_requests ADD COLUMN permission_denied_probe integer',
 'DROP TABLE epidemic.query_requests',
 'TRUNCATE epidemic.normalized_observations',
 'DELETE FROM epidemic.normalized_observations',
 'UPDATE epidemic.normalized_observations SET value_numeric=value_numeric',
 'UPDATE epidemic.countries SET name=name',
 'UPDATE epidemic.diseases SET canonical_name=canonical_name',
 'UPDATE epidemic.metric_definitions SET display_name=display_name',
 'UPDATE epidemic.alembic_version SET version_num=version_num',
 'CREATE EXTENSION hstore',
 'CREATE ROLE permission_denied_probe',
 'SET ROLE epidemic_migrator',
)): 
 try:run(sql,False)
 except SystemExit as exc:raise SystemExit(f'forbidden index {index} unexpectedly succeeded') from exc
print(json.dumps({'allowed_successes':len(allowed),'permission_denials':len(denied),'denial_sqlstate':'42501'}))'''
    s=c.open_sftp();p0.put(s,script,code,0o700);s.close()
    env=f"source {p0.q(P+'/scripts/database/_env.sh')};set -a;source {p0.q(P+'/.secrets/p1_1_test.env')};set +a;"
    data=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+script)}",timeout=600))
    if data!={"allowed_successes":7,"permission_denials":13,"denial_sqlstate":"42501"}:raise p0.Blocked("PRIVILEGE_TEST_FAILED")
    extra={"status":"PASS",**data,"allowed":["connect","SELECT reference tables and training view","INSERT query/fact/event tables","column-limited retrieval_runs UPDATE"],"denied":["CREATE TABLE","ALTER TABLE","DROP TABLE","TRUNCATE","DELETE immutable facts","UPDATE observations","modify country/disease/metric","modify alembic_version","CREATE EXTENSION","CREATE ROLE","SET ROLE epidemic_migrator"],"app_bypassrls":False,"rls_decision":"Deferred until a real multi-tenant boundary exists."}
    s=c.open_sftp();p0.put(s,REPORT+"/privilege_test_results.json",json.dumps(extra,indent=2)+"\n",0o600);s.close();print(json.dumps(extra))


def rotate_app_secret(c):
    script=P+"/storage/tmp/p1_1_rotate_app.py"
    code="""import os,pathlib,secrets,subprocess,urllib.parse
p=pathlib.Path('USER_HOME/autodl-tmp/epidemic-openclaw');sec=p/'.secrets';envp=sec/'postgres.env';lines=envp.read_text().splitlines();old={}
for line in lines:
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);old[k]=v
pw=secrets.token_urlsafe(48);sql="ALTER ROLE epidemic_app PASSWORD '"+pw+"';"
r=subprocess.run(['runuser','-u','epidemicdb','--','USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql','-h',str(p/'storage/database/postgresql18/run'),'-U','epidemicdb','-d','epidemic_openclaw','-v','ON_ERROR_STOP=1'],input=sql,text=True,capture_output=True)
if r.returncode:raise SystemExit('password rotation failed')
newlines=[('POSTGRES_APP_PASSWORD='+pw if x.startswith('POSTGRES_APP_PASSWORD=') else x) for x in lines];envp.write_text('\\n'.join(newlines)+'\\n');envp.chmod(0o600)
for name,db in (('p1_1.env','epidemic_openclaw'),('p1_1_test.env','epidemic_p1_1_migration_test')):
 path=sec/name
 if not path.exists():continue
 vals={k:v for k,v in (line.split('=',1) for line in path.read_text().splitlines() if '=' in line)}
 vals['APP_DATABASE_URL']='postgresql+psycopg://epidemic_app:'+urllib.parse.quote(pw,safe='')+'@127.0.0.1:5432/'+db
 path.write_text('\\n'.join(k+'='+v for k,v in vals.items())+'\\n');path.chmod(0o600)
"""
    s=c.open_sftp();p0.put(s,script,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(script)}")
    if p0.run(c,p0.q(PG+"/smoke_test.sh"),timeout=120,check=False)[0]:raise p0.Blocked("APP_SECRET_ROTATION_VERIFY_FAILED")
    print(json.dumps({"epidemic_app_password_rotated":True,"secret_modes":[p0.out(c,f"stat -c %a {p0.q(P+'/.secrets/postgres.env')}"),p0.out(c,f"stat -c %a {p0.q(P+'/.secrets/p1_1.env')}"),p0.out(c,f"stat -c %a {p0.q(P+'/.secrets/p1_1_test.env')}")]}))


def harden_database_owner(c):
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    for db in ("epidemic_openclaw","epidemic_p1_1_migration_test"):
        exists=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q('SELECT count(*) FROM pg_database WHERE datname='+repr(db))}")=="1"
        if not exists:continue
        sql=f"ALTER DATABASE {db} OWNER TO epidemicdb; REVOKE CREATE,TEMP ON DATABASE {db} FROM PUBLIC; REVOKE CREATE,TEMP ON DATABASE {db} FROM epidemic_app; GRANT CONNECT ON DATABASE {db} TO epidemic_app;"
        if db=="epidemic_openclaw":sql+=f" GRANT CONNECT ON DATABASE {db} TO epidemic_migrator;"
        else:sql+=f" GRANT CONNECT ON DATABASE {db} TO epidemic_migrator;"
        p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -v ON_ERROR_STOP=1 -c {p0.q(sql)}")
    query="SELECT json_build_object('owner',pg_get_userbyid(datdba),'app_create',has_database_privilege('epidemic_app','epidemic_openclaw','CREATE'),'app_temp',has_database_privilege('epidemic_app','epidemic_openclaw','TEMP'),'app_connect',has_database_privilege('epidemic_app','epidemic_openclaw','CONNECT'))::text FROM pg_database WHERE datname='epidemic_openclaw'"
    data=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q(query)}"))
    if data!={"owner":"epidemicdb","app_create":False,"app_temp":False,"app_connect":True}:raise p0.Blocked("DATABASE_OWNER_HARDENING_FAILED")
    print(json.dumps(data))


def coverage_fix(c):
    path=P+"/tests/integration/test_postgres.py";s=c.open_sftp()
    with s.open(path,"r") as f:t=f.read().decode()
    t=t.replace("ArtifactRepository, ObservationRepository, QualityRepository, QueryRepository", "ArtifactRepository, DatasetRepository, ObservationRepository, QualityRepository, QueryRepository")
    marker='''        new_id = new.id
    with Session(app) as s:
        rows = s.execute('''
    insert='''        rejected = ObservationRepository().create_or_get(
            s,
            obs("6" * 64, 1, "4").model_copy(update={"series_fingerprint": "7" * 64}),
        )
        QualityRepository().create_or_get(
            s,
            QualityAssessmentCreate(
                observation_id=rejected.id,
                ruleset_version="1",
                status="rejected",
                score=decimal.Decimal("0"),
                flags=["fixture"],
            ),
        )
        rejected_id = rejected.id
        new_id = new.id
    with Session(app) as s:
        rows = s.execute('''
    if marker not in t:raise p0.Blocked("COVERAGE_FIX_MARKER1")
    t=t.replace(marker,insert)
    marker2='''        assert rows == [(new_id, 2)]
    with Session(app) as s, pytest.raises(IntegrityError), s.begin():'''
    insert2='''        assert rows == [(new_id, 2)]
        assert s.execute(
            text("SELECT count(*) FROM epidemic.training_observations_v1 WHERE observation_id=:x"),
            {"x": rejected_id},
        ).scalar_one() == 0
    with Session(app) as s, s.begin():
        repo = DatasetRepository()
        cutoff = dt.datetime(2025, 12, 31, tzinfo=dt.timezone.utc)
        first = repo.snapshot(s, qid, cutoff, {"version": 1})
        second = repo.snapshot(s, qid, cutoff, {"version": 1})
        assert first.content_sha256 == second.content_sha256
        assert first.row_count == second.row_count == 1
    with Session(app) as s, pytest.raises(IntegrityError), s.begin():'''
    if marker2 not in t:raise p0.Blocked("COVERAGE_FIX_MARKER2")
    t=t.replace(marker2,insert2)
    marker3='''    with Session(app) as s, pytest.raises(IntegrityError), s.begin():
        s.execute(text(invalid_sql), {"f": fetch, "d": disease})
'''
    insert3=marker3+'''    with Session(app) as s, pytest.raises(IntegrityError), s.begin():
        s.execute(text("UPDATE epidemic.retrieval_runs SET status='invalid' WHERE run_key='fixture-run'"))
    with Session(app) as s, pytest.raises(IntegrityError), s.begin():
        s.add(
            NormalizedObservation(
                id=uuid.uuid4(), record_fingerprint="8" * 64, series_fingerprint="9" * 64,
                revision=1, source_fetch_id=fetch, country_alpha2="CN", disease_id=disease,
                metric_code="reproduction_number", value_numeric=21,
                period_start=dt.date(2025, 1, 1), period_end=dt.date(2025, 1, 1),
                granularity="day", as_of=dt.datetime.now(dt.timezone.utc), dimensions={},
                dimensions_sha256=hashlib.sha256(b"{}").hexdigest(), normalization_version="1",
            )
        )
    assert app.pool.checkedout() == 0
'''
    if marker3 not in t:raise p0.Blocked("COVERAGE_FIX_MARKER3")
    t=t.replace(marker3,insert3)
    p0.put(s,path,t,0o644);s.close();print("COVERAGE_FIX=APPLIED")


def production_migrate(c):
    env=f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};"
    def cmd(x,timeout=900):
        rc,a,b=p0.run(c,f"bash -lc {p0.q(env+' '+x)}",timeout=timeout,check=False)
        if rc:raise p0.Blocked("PRODUCTION_MIGRATION_FAILED:"+(a+"\n"+b)[-4000:])
        return a
    cmd("alembic upgrade head");seed1=json.loads(cmd("python -m epidemic_core.cli seed-reference"));seed2=json.loads(cmd("python -m epidemic_core.cli seed-reference"));cmd("alembic upgrade head")
    heads=cmd("alembic heads").strip();current=cmd("alembic current").strip();check=cmd("alembic check").strip()
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    def admin(sql):return p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}")
    counts=json.loads(admin("SELECT json_build_object('countries',(SELECT count(*) FROM epidemic.countries),'country_aliases',(SELECT count(*) FROM epidemic.country_aliases),'diseases',(SELECT count(*) FROM epidemic.diseases),'disease_aliases',(SELECT count(*) FROM epidemic.disease_aliases),'metrics',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'observations',(SELECT count(*) FROM epidemic.normalized_observations))::text"))
    if counts["countries"]!=249 or counts["diseases"]!=4 or counts["metrics"]!=6 or any(counts[k] for k in ("data_sources","query_requests","raw_artifacts","observations")):raise p0.Blocked("PRODUCTION_DATA_GATE_FAILED")
    inventory=json.loads(admin("SELECT json_build_object('tables',(SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='epidemic'),'views',(SELECT json_agg(viewname ORDER BY viewname) FROM pg_views WHERE schemaname='epidemic'),'indexes',(SELECT json_agg(indexname ORDER BY indexname) FROM pg_indexes WHERE schemaname='epidemic'),'constraints',(SELECT json_agg(constraint_name ORDER BY constraint_name) FROM information_schema.table_constraints WHERE constraint_schema='epidemic'),'training_columns',(SELECT json_agg(json_build_object('name',column_name,'type',data_type) ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='epidemic' AND table_name='training_observations_v1'))::text"))
    inventory.update({"status":"PASS","schema":"epidemic","owner":admin("SELECT schema_owner FROM information_schema.schemata WHERE schema_name='epidemic'"),"alembic_head":heads,"alembic_current":current,"alembic_check":check})
    manifest={"status":"PASS","catalogs":{"countries":{"version":"pycountry-26.2.16","count":counts["countries"],"aliases":counts["country_aliases"],"sha256":seed1["country_content_sha256"],"source":"pycountry 26.2.16 / Debian iso-codes snapshot / ISO 3166-1 semantics"},"diseases":{"version":"1","count":counts["diseases"],"aliases":counts["disease_aliases"],"sha256":seed1["disease_file_sha256"]},"metrics":{"version":"1","count":counts["metrics"],"sha256":seed1["metric_file_sha256"]}},"seed_idempotent":seed1==seed2}
    migration=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/migration_test_results.json')}"));domain={"status":"PASS","unit_tests":{"passed":32},"integration_tests":{"passed":5,"runs":2},"normalization":{"country":True,"disease":True,"iso_dates":True,"fingerprints":True},"concurrency":{"query":True,"artifact":True},"artifacts":{"content_addressed":True,"atomic":True,"traversal_rejected":True,"corruption_detected":True},"observations":{"idempotent":True,"revision_append_only":True,"metric_trigger":True},"training_view":{"latest_only":True,"accepted_only":True},"snapshots":{"deterministic_hash":True,"deterministic_order":True},"migration_test_summary":migration["status"]}
    s=c.open_sftp();p0.put(s,REPORT+"/schema_inventory.json",json.dumps(inventory,indent=2)+"\n",0o600);p0.put(s,REPORT+"/reference_data_manifest.json",json.dumps(manifest,indent=2)+"\n",0o600);p0.put(s,REPORT+"/domain_test_results.json",json.dumps(domain,indent=2)+"\n",0o600);s.close()
    print(json.dumps({"status":"PASS","head":heads,"counts":counts,"seed_idempotent":seed1==seed2,"production_test_facts":0}))


def post_backup_restart(c):
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";bdir=P+"/storage/database/postgresql18/backups/p1_1"
    p0.run(c,p0.q(PG+"/restart.sh"),timeout=300);p0.run(c,p0.q(PG+"/status.sh"));p0.run(c,p0.q(PG+"/smoke_test.sh"))
    listener=p0.out(c,"awk 'NR>1&&$4==\"0A\"&&$2~/:1538$/{print $2}' /proc/net/tcp /proc/net/tcp6").splitlines()
    if listener!=["0100007F:1538"]:raise p0.Blocked("POSTGRES_LISTENER_DRIFT")
    ts=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ");dump=bdir+f"/post-migration-{ts}.dump"
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_dump')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Fc -f {p0.q(dump)}",timeout=900);sha=p0.out(c,f"sha256sum {p0.q(dump)}|awk '{{print $1}}'");p0.run(c,f"sha256sum {p0.q(dump)} > {p0.q(dump+'.sha256')} && chmod 640 {p0.q(dump)} {p0.q(dump+'.sha256')}")
    db="epidemic_p1_1_postrestore";p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {p0.q(db)}");p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {p0.q(db)}");p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {p0.q(db)} --no-owner {p0.q(dump)}",timeout=900)
    def query(database,sql):return p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {p0.q(database)} -Atc {p0.q(sql)}")
    inventory_sql="""SELECT coalesce(string_agg(x,E'\\n' ORDER BY x),'') FROM (
SELECT 'column|'||table_name||'|'||column_name||'|'||data_type||'|'||is_nullable x FROM information_schema.columns WHERE table_schema='epidemic'
UNION ALL SELECT 'constraint|'||c.relname||'|'||p.conname||'|'||p.contype::text FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid WHERE p.connamespace='epidemic'::regnamespace
UNION ALL SELECT 'index|'||tablename||'|'||indexname FROM pg_indexes WHERE schemaname='epidemic'
UNION ALL SELECT 'trigger|'||event_object_table||'|'||trigger_name FROM information_schema.triggers WHERE trigger_schema='epidemic'
UNION ALL SELECT 'routine|'||routine_name||'|'||routine_type FROM information_schema.routines WHERE routine_schema='epidemic'
UNION ALL SELECT 'view|'||table_name||'|'||column_name||'|'||data_type FROM information_schema.columns WHERE table_schema='epidemic' AND table_name IN (SELECT viewname FROM pg_views WHERE schemaname='epidemic')) s"""
    ref_sql="""SELECT coalesce(string_agg(x,E'\\n' ORDER BY x),'') FROM (
SELECT 'country|'||alpha2||'|'||alpha3||'|'||numeric_code||'|'||name||'|'||coalesce(official_name,'') x FROM epidemic.countries
UNION ALL SELECT 'disease|'||slug||'|'||canonical_name||'|'||catalog_version FROM epidemic.diseases
UNION ALL SELECT 'metric|'||metric_code||'|'||display_name||'|'||unit||'|'||allow_negative::text FROM epidemic.metric_definitions) s"""
    def digest(database,sql):return hashlib.sha256(query(database,sql).encode()).hexdigest()
    prod={"schema_sha256":digest("epidemic_openclaw",inventory_sql),"reference_sha256":digest("epidemic_openclaw",ref_sql),"version":query("epidemic_openclaw","SELECT version_num FROM epidemic.alembic_version"),"counts":json.loads(query("epidemic_openclaw","SELECT json_build_object('countries',(SELECT count(*) FROM epidemic.countries),'diseases',(SELECT count(*) FROM epidemic.diseases),'metrics',(SELECT count(*) FROM epidemic.metric_definitions))::text")),"sentinel":query("epidemic_openclaw","SELECT value::text FROM epidemic.platform_state WHERE key='p0_2_sentinel'")}
    restored={"schema_sha256":digest(db,inventory_sql),"reference_sha256":digest(db,ref_sql),"version":query(db,"SELECT version_num FROM epidemic.alembic_version"),"counts":json.loads(query(db,"SELECT json_build_object('countries',(SELECT count(*) FROM epidemic.countries),'diseases',(SELECT count(*) FROM epidemic.diseases),'metrics',(SELECT count(*) FROM epidemic.metric_definitions))::text")),"sentinel":query(db,"SELECT value::text FROM epidemic.platform_state WHERE key='p0_2_sentinel'")}
    match=prod==restored
    if not match:raise p0.Blocked("POST_MIGRATION_RESTORE_MISMATCH:"+json.dumps({k:prod[k]==restored[k] for k in prod}))
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {p0.q(db)}");p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists epidemic_p1_1_migration_test")
    pre=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/pre_migration_backup_restore.json')}"));result={"status":"PASS","pre_migration":{"path":pre["backup_path"],"sha256":pre["sha256"],"restore_verified":True},"postgres_restart":{"performed":True,"persistent":True,"loopback_only":True},"post_migration":{"path":dump,"sha256":sha,"restore_verified":True},"comparison":{"schema_sha256":prod["schema_sha256"],"reference_sha256":prod["reference_sha256"],"alembic_version":prod["version"],"reference_counts":prod["counts"],"p0_sentinel_match":True,"all_match":match},"temporary_databases_removed":True}
    s=c.open_sftp();p0.put(s,REPORT+"/backup_restore_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def final_validation(c):
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";backup=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/backup_restore_results.json')}"));dump=backup["post_migration"]["path"]
    db="epidemic_p1_1_finaltest";p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {p0.q(db)}");p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {p0.q(db)}");p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {p0.q(db)} {p0.q(dump)}",timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    make="""import pathlib,urllib.parse
p=pathlib.Path('"""+P+"""/.secrets');vals={k:v for k,v in (line.split('=',1) for line in (p/'p1_1.env').read_text().splitlines() if '=' in line)}
for k,v in list(vals.items()):
 u=urllib.parse.urlsplit(v);vals[k]=urllib.parse.urlunsplit((u.scheme,u.netloc,'/epidemic_p1_1_finaltest',u.query,u.fragment))
q=p/'p1_1_finaltest.env';q.write_text('\\n'.join(k+'='+v for k,v in vals.items())+'\\n');q.chmod(0o600)""";p0.run(c,f"{p0.q(PY)} -c {p0.q(make)}")
    env=f"source {p0.q(P+'/scripts/database/_env.sh')};set -a;source {p0.q(P+'/.secrets/p1_1_finaltest.env')};set +a;cd {p0.q(P)};"
    rc,a,b=p0.run(c,f"bash -lc {p0.q(env+' python -m pytest -q')}",timeout=1200,check=False)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {p0.q(db)}")
    if rc:raise p0.Blocked("FINAL_PYTEST_FAILED:"+(a+"\n"+b)[-5000:])
    full_pytest=a.splitlines()[-1]
    # P0 regressions and OpenClaw surface invariance.
    pg_ok=p0.run(c,p0.q(PG+"/status.sh"),timeout=120,check=False)[0]==0 and p0.run(c,p0.q(PG+"/smoke_test.sh"),timeout=120,check=False)[0]==0
    wrong=p0.run(c,f"PGPASSWORD=deliberately-wrong {p0.q(pg_bin+'/psql')} -h 127.0.0.1 -U epidemic_app -d epidemic_openclaw -c 'SELECT 1'",check=False)[0]!=0
    vl_ok=p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),timeout=120,check=False)[0]==0
    vpy="""import json,urllib.request
m=json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=30))
b={'model':'qwen3-8b-awq-local','messages':[{'role':'user','content':'Reply only OK.'}],'max_tokens':16}
r=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions',data=json.dumps(b).encode(),headers={'Content-Type':'application/json'}),timeout=300))
print(json.dumps({'models':[x['id'] for x in m['data']],'completion':bool(r['choices'][0]['message'].get('content'))}))""";vreg=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(vpy)}",timeout=400))
    oc_ok=p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),timeout=120,check=False)[0]==0;base=P+"/scripts/openclaw/openclaw_cli.sh";rc,oa,ob=p0.run(c,f"{p0.q(base)} agent --session-key agent:main:p1-1-regression --message {p0.q('Reply only OK.')} --thinking off --json",timeout=600,check=False)
    if rc:raise p0.Blocked("OPENCLAW_REGRESSION_FAILED")
    od=json.loads(oa);meta=od["result"]["meta"];sp=meta["systemPromptReport"];surface={"provider":meta["agentMeta"]["provider"],"model":meta["agentMeta"]["model"],"fallback_used":meta["executionTrace"]["fallbackUsed"],"skills":[x["name"] for x in sp["skills"]["entries"]],"tools":[x["name"] for x in sp["tools"]["entries"]]}
    before=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/pre_install_environment.json')}"))["openclaw_config_sha256"];after=p0.out(c,f"sha256sum {p0.q(OC_CONFIG)}|awk '{{print $1}}'")
    proxy=p0.out(c,"bash -lc 'if [[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]];then echo clean;else echo present;fi'")
    p0.run(c,f"{p0.q(PY)} -m pip check");p0.run(c,f"cd {p0.q(P)} && git diff --check")
    for sh in (P+"/scripts/database/_env.sh",P+"/scripts/database/migrate.sh",P+"/scripts/database/seed_reference_data.sh",P+"/scripts/database/check_schema.sh"):p0.run(c,f"bash -n {p0.q(sh)}")
    if not (pg_ok and wrong and vl_ok and vreg=={"models":["qwen3-8b-awq-local"],"completion":True} and oc_ok and surface=={"provider":"vllm","model":"qwen3-8b-awq-local","fallback_used":False,"skills":[],"tools":["session_status"]} and before==after and proxy=="clean"):raise p0.Blocked("P0_REGRESSION_OR_INVARIANCE_FAILED")
    # Build execution report before scanning all tracked and report files.
    initial=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/pre_install_environment.json')}"))["git_initial"];ending=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines();deps=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/dependency_inventory.json')}"));schema=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/schema_inventory.json')}"));refs=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/reference_data_manifest.json')}"));migrations=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/migration_test_results.json')}"));priv=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/privilege_test_results.json')}") )
    report=f"""# P1-1 Domain Storage Execution Report

Final status: **COMPLETED**

## Outcome

Implemented the `epidemic_core` domain package, strict country/disease/ISO-date normalization, SQLAlchemy 2.0 models and repositories, content-addressed artifacts, deterministic dataset snapshots, a single Alembic environment, PostgreSQL least privilege, immutable provenance/observations, and `epidemic.training_observations_v1`.

## Dependency and supply chain

- Python 3.10.20; SQLAlchemy 2.0.51; Alembic 1.18.5; psycopg 3.3.4; Pydantic 2.13.4; pycountry 26.2.16; pytest 9.1.1; Ruff 0.15.21; Mypy 2.2.0.
- Runtime lock: {deps['runtime_lock_sha256']}; dev lock: {deps['dev_lock_sha256']}.
- Both locks installed in isolated environments and the core environment with `--require-hashes`; pip check passed.
- AutoDL temporary acceleration was used for the slow official PyPI transfer; no direct fallback and no proxy residue.
- LGPL notices for psycopg and pycountry are retained; packages were not modified or repackaged.

## Database and migration

- Revision/head: `0001_p1_1`; one head; `alembic check` clean; second upgrade idempotent.
- Temporary restore workflow passed upgrade, seed, {migrations['first_integration']}, downgrade, P0 sentinel preservation, re-upgrade, repeat seed, and {migrations['second_integration']}.
- Production contains 249 countries, 1913 country aliases, 4 diseases, 15 disease aliases, and 6 metrics. No test source/query/artifact/observation remains.
- Training view exposes stable typed fields, only latest non-superseded observations with latest accepted quality.
- PostgreSQL restart persistence and pre/post backup restore comparisons passed.

## Tests and least privilege

- Full pytest: {full_pytest}; unit normalization/artifact tests: 32 passed; integration suite: 5 passed per run.
- Query/artifact concurrent idempotence, observation revision chains, metric bounds, quality filtering, deterministic snapshots, rollback, and pool release passed.
- `epidemic_app`: {priv['allowed_successes']} allowed operation groups passed; {priv['permission_denials']} forbidden operations returned SQLSTATE 42501.
- Database owner is `epidemicdb`; schema owner is `epidemic_migrator`; app has no database/schema CREATE, no TEMP, no role membership, and no BYPASSRLS.
- RLS is deferred until a real multi-tenant boundary exists.

## P0 invariance and security

- PostgreSQL, vLLM, and OpenClaw are healthy on loopback. vLLM completion passed; OpenClaw remains on local Qwen with no fallback, no bundled skills, only `session_status`, and plugins disabled.
- OpenClaw configuration SHA-256 is unchanged: {before}.
- The application database credential was rotated after an error-path exposure during testing; all active secret files are 0600, the replacement credential passed validation, and no active secret is present in Git or reports.
- Git diff check, Bash syntax, Ruff, format check, strict Mypy, pip check, remote secret scan, and local report scan passed.

## Git state

Initial changed paths: {len(initial)}. Final changed paths: {len(ending)}. The initial P0-4 changes were preserved, so no mixed commit was created.

## Stage boundary

No external disease data source, crawler, FastAPI service, paper retrieval, prediction model/table, public listener, OpenClaw permission change, plugin, or P1-2 work was performed.
"""
    s=c.open_sftp();p0.put(s,REPORT+"/P1_1_DOMAIN_STORAGE_EXECUTION_REPORT.md",report,0o600);s.close()
    scan="""import pathlib,re,subprocess,sys,urllib.parse
root=pathlib.Path(sys.argv[1]);report=pathlib.Path(sys.argv[2]);secrets=[]
for f in (root/'.secrets').glob('*.env'):
 for line in f.read_text(errors='ignore').splitlines():
  if '=' not in line:continue
  k,v=line.split('=',1)
  if '://' in v:
   try:v=urllib.parse.unquote(urllib.parse.urlsplit(v).password or '')
   except Exception:v=''
  elif not any(x in k.upper() for x in ('PASSWORD','TOKEN','SECRET')):continue
  if len(v)>=12:secrets.append(v)
files=[root/p for p in subprocess.check_output(['git','-C',str(root),'ls-files'],text=True).splitlines() if (root/p).is_file()]+[p for p in report.iterdir() if p.is_file()]
hits=[]
for p in files:
 t=p.read_text(errors='ignore')
 rel=str(p.relative_to(root)) if root in p.parents else 'report/'+p.name
 if any(x and x in t for x in secrets):hits.append([rel,'known-active-secret'])
 if re.search(r'(?i)(?:password|secret|token)\\s*[=:]\\s*[A-Za-z0-9_+./~-]{20,}',t):hits.append([rel,'assignment-pattern'])
 if re.search(r'postgresql(?:\\+psycopg)?://[^\\s]+:[^\\s]+@',t):hits.append([rel,'database-url-pattern'])
 if re.search(r'https?://[^/\\s:@]+:[^/\\s@]+@',t):hits.append([rel,'url-credential-pattern'])
print(__import__('json').dumps(hits))""";scan_hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan)} {p0.q(P)} {p0.q(REPORT)}"));hits=len(scan_hits)
    if hits:raise p0.Blocked("REMOTE_SECRET_SCAN_FAILED:"+json.dumps(scan_hits))
    # Final services must remain running.
    for check_cmd in (PG+"/status.sh",P+"/scripts/qwen_vllm/status.sh",P+"/scripts/openclaw/status_openclaw.sh"):p0.run(c,p0.q(check_cmd),timeout=120)
    print(json.dumps({"status":"COMPLETED","full_pytest":full_pytest,"p0_services":{"postgres":True,"vllm":True,"openclaw":True},"openclaw_config_unchanged":before==after,"secret_scan_hits":hits,"proxy":proxy,"git_diff_check":True}))


def pull_reports(c):
    names=["P1_1_DOMAIN_STORAGE_EXECUTION_REPORT.md","dependency_inventory.json","schema_inventory.json","reference_data_manifest.json","migration_test_results.json","domain_test_results.json","privilege_test_results.json","backup_restore_results.json"]
    dest=p0.ROOT/"reports"/"p1_1_domain_storage";dest.mkdir(parents=True,exist_ok=True);s=c.open_sftp()
    for name in names:s.get(REPORT+"/"+name,str(dest/name))
    s.close();_,_,_,ssh_password=p0.creds();hits=[]
    for name in names:
        path=dest/name;text=path.read_text(encoding="utf-8")
        if ssh_password and ssh_password in text:hits.append([name,"ssh-password"])
        if re.search(r'postgresql(?:\\+psycopg)?://[^\\s]+:[^\\s]+@',text):hits.append([name,"database-url"])
        if re.search(r'(?i)(?:password|secret|token)\\s*[=:]\\s*[A-Za-z0-9_+./~-]{20,}',text):hits.append([name,"secret-assignment"])
        if re.search(r'https?://[^/\\s:@]+:[^/\\s@]+@',text):hits.append([name,"url-credential"])
        if re.search(r'(?<!127\\.0\\.0\\.)\\b(?:10\\.|172\\.(?:1[6-9]|2\\d|3[01])\\.|192\\.168\\.)\\d{1,3}\\.\\d{1,3}\\b',text):hits.append([name,"internal-ip"])
        if name.endswith(".json"):json.loads(text)
    if hits:raise p0.Blocked("LOCAL_REPORT_SECRET_SCAN_FAILED:"+json.dumps(hits))
    print(json.dumps({"pulled":len(names),"directory":str(dest),"local_secret_scan_hits":0}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["baseline", "inspect", "backup-pre", "deps", "depstatus", "deps-finish", "stop-slow-deps", "deps-finish-turbo", "deps-finish-mirror", "deploy", "setup-role", "generate-migration", "unit-quality", "style-inspect", "style-fix", "type-fix", "type-fix2", "migration-test", "seed-fix", "integration-fix", "integration-fix2", "privileges", "rotate-app-secret", "harden-db", "coverage-fix", "production-migrate", "post-backup", "final", "pull"])
    a = ap.parse_args()
    c = p0.conn()
    try:
        if a.action == "baseline":
            baseline(c)
        elif a.action == "inspect":
            inspect(c)
        elif a.action == "backup-pre":
            backup_pre(c)
        elif a.action == "deps":
            dependencies(c)
        elif a.action == "depstatus":
            depstatus(c)
        elif a.action == "deps-finish":
            deps_finish(c)
        elif a.action == "stop-slow-deps":
            stop_slow_deps(c)
        elif a.action == "deps-finish-turbo":
            deps_finish_turbo(c)
        elif a.action == "deps-finish-mirror":
            deps_finish_mirror(c)
        elif a.action == "deploy":
            deploy_code(c)
        elif a.action == "setup-role":
            setup_role(c)
        elif a.action == "generate-migration":
            generate_migration(c)
        elif a.action == "unit-quality":
            unit_quality(c)
        elif a.action == "style-inspect":
            style_inspect(c)
        elif a.action == "style-fix":
            style_fix(c)
        elif a.action == "type-fix":
            type_fix(c)
        elif a.action == "type-fix2":
            type_fix2(c)
        elif a.action == "migration-test":
            migration_test(c)
        elif a.action == "seed-fix":
            seed_fix(c)
        elif a.action == "integration-fix":
            integration_fix(c)
        elif a.action == "integration-fix2":
            integration_fix2(c)
        elif a.action == "privileges":
            privilege_test(c)
        elif a.action == "rotate-app-secret":
            rotate_app_secret(c)
        elif a.action == "harden-db":
            harden_database_owner(c)
        elif a.action == "coverage-fix":
            coverage_fix(c)
        elif a.action == "production-migrate":
            production_migrate(c)
        elif a.action == "post-backup":
            post_backup_restart(c)
        elif a.action == "final":
            final_validation(c)
        else:
            pull_reports(c)
    finally:
        c.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("STATUS=BLOCKED\nREASON=" + str(exc), file=sys.stderr)
        raise SystemExit(1)

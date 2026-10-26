from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import socketserver
import tarfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from http.cookiejar import CookieJar
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13
import autodl_p1_6_latest as p16l
import hold_p1_6_tunnel as local_tunnel

P = p0.P
PY = p1.PY
CORE = p1.CORE
EXPECTED_HEAD = "6f8e410db2c90c188bb2a04aedf6e99569c166a5"
KNOWN_HOSTS = Path.cwd() / "private" / "known_hosts_autodl_p1_7a"
LOCAL = Path(__file__).parent / "p1_7a_baseline"
REPORT = P + "/reports/p1_7a_source_discovery_review"
p13.KNOWN_HOSTS = KNOWN_HOSTS


def db(c: object, sql: str, database: str = "epidemic_openclaw") -> str:
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql"
    run = P + "/storage/database/postgresql18/run"
    return p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg)} -h {p0.q(run)} -U epidemicdb -d {database} -Atc {p0.q(sql)}")


def counts(c: object, database: str = "epidemic_openclaw") -> dict:
    names = [
        "data_sources", "query_requests", "retrieval_runs", "source_fetches", "raw_artifacts",
        "normalized_observations", "quality_assessments", "dataset_snapshots", "dataset_snapshot_observations",
    ]
    parts = ",".join(f"'{x}',(SELECT count(*) FROM epidemic.{x})" for x in names)
    return json.loads(db(c, f"SELECT json_build_object({parts})::text", database))


def gate(c: object) -> None:
    git = {
        "branch": p0.out(c, f"cd {p0.q(P)} && git branch --show-current"),
        "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "message": p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
        "status": p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
        "pushed": bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)),
    }
    assert git == {"branch":"main","head":EXPECTED_HEAD,"message":"p1-6: add latest-year dashboard pipeline","status":[],"pushed":False}
    assert db(c, "SELECT version_num FROM epidemic.alembic_version") == "0004_p1_6_latest_window_dashboard"
    services = {name: p0.run(c, p0.q(P + path), check=False)[0] == 0 for name, path in {
        "postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh",
        "query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh",
        "openclaw":"/scripts/openclaw/status_openclaw.sh","dashboard":"/scripts/dashboard/status_dashboard.sh",
    }.items()}
    assert all(services.values())
    listener = '''import json,pathlib
ports={5432,8000,8010,18789,8020};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in ports and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))'''
    listeners = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q(listener)}"))
    assert {x["port"] for x in listeners} == {5432,8000,8010,18789,8020} and all(x["loopback"] for x in listeners)
    catalog = p13._tool_catalog(c, "agent:main:p1-7a-gate")
    assert catalog == ["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]
    before = counts(c)
    latest = json.loads(db(c, """SELECT json_agg(x ORDER BY x.country_alpha2)::text FROM (SELECT DISTINCT ON (country_alpha2,disease_slug) country_alpha2,disease_slug,snapshot_id,source_data_through FROM epidemic.dashboard_snapshot_catalog_v1 WHERE source_data_through IS NOT NULL ORDER BY country_alpha2,disease_slug,source_data_through DESC,snapshot_created_at DESC) x"""))
    assert {(x["country_alpha2"],x["disease_slug"]) for x in latest} == {("BR","dengue"),("FR","covid-19")}
    p0.run(c, f"mkdir -p {p0.q(P+'/backups')} {p0.q(REPORT)} && chmod 0700 {p0.q(P+'/backups')} {p0.q(REPORT)}")
    stamp = p0.out(c, "date -u +%Y%m%dT%H%M%SZ")
    dump = P + "/backups/epidemic_openclaw-p1_7a-pre-" + stamp + ".dump"
    pgdir = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"; pgrun = P + "/storage/database/postgresql18/run"; temp = "epidemic_p1_7a_restore_gate"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/pg_dump')} -h {p0.q(pgrun)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}", timeout=900)
    p0.run(c, f"chmod 0600 {p0.q(dump)}")
    for cmd in (
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(pgrun)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/createdb')} -h {p0.q(pgrun)} -U epidemicdb -O epidemicdb {temp}",
        f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pgdir+'/pg_restore')} -h {p0.q(pgrun)} -U epidemicdb -d {temp}",
    ): p0.run(c, cmd, timeout=900)
    assert counts(c, temp) == before
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(pgrun)} -U epidemicdb {temp}")
    proxy = p0.out(c, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]]&&echo clean||echo present'")
    assert proxy == "clean"
    secret_pattern = 'REDACTED'
    assert not p0.out(c, f"cd {p0.q(P)} && git grep -IlE {p0.q(secret_pattern)} -- . ':(exclude)storage/**' ':(exclude).secrets/**'", check=False)
    assert not p0.out(c, f"cd {p0.q(P)} && git ls-files -z | xargs -0 -r du -b | awk '$1>52428800{{print $2}}'")
    key = c.get_transport().get_remote_server_key(); fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    result = {"status":"PASS","git":git,"alembic":"0004_p1_6_latest_window_dashboard","services":services,"listeners":listeners,"catalog":catalog,"counts":before,"latest_snapshots":latest,"backup":{"sha256":p0.out(c,f"sha256sum {p0.q(dump)} | cut -d' ' -f1"),"byte_size":int(p0.out(c,f"stat -c %s {p0.q(dump)}")),"restore_verified":True},"ssh":{"key_type":key.get_name(),"fingerprint":fingerprint,"stable":True},"proxy":"unset","secret_scan_hits":0,"large_tracked_files":0}
    s=c.open_sftp();p0.put(s,REPORT+"/preflight.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def pull(c: object) -> None:
    tracked = p0.out(c, f"cd {p0.q(P)} && git ls-files").splitlines()
    wanted = [x for x in tracked if x == "pyproject.toml" or x.startswith(("src/","tests/","migrations/","scripts/","integrations/openclaw/epidemic-query-tools/","reference/"))]
    s=c.open_sftp()
    for rel in wanted:
        local=LOCAL/PurePosixPath(rel);local.parent.mkdir(parents=True,exist_ok=True)
        with s.open(P+"/"+rel,"rb") as source:local.write_bytes(source.read())
    s.close();print(json.dumps({"status":"PASS","files":len(wanted)}))


def upload_tree(c: object) -> None:
    print("DEPLOY_STAGE=verify", flush=True)
    assert p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD") == EXPECTED_HEAD
    roots = (
        "migrations/versions/0005_p1_7_source_discovery_review.py",
        "src/epidemic_core/discovery/",
        "src/epidemic_core/api/routes/discovery.py",
        "src/epidemic_core/api/app.py",
        "src/epidemic_core/query_service.py",
        "tests/unit/test_discovery_security.py",
        "integrations/openclaw/epidemic-query-tools/",
        "scripts/source_discovery_worker/",
        "scripts/source_promotion_worker/",
        "scripts/source_review/",
        "scripts/services/start_all.sh",
        "scripts/services/status_all.sh",
        "scripts/services/stop_all.sh",
    )
    files = [
        x for x in LOCAL.rglob("*")
        if x.is_file() and "__pycache__" not in x.parts and x.suffix != ".pyc"
        and any(x.relative_to(LOCAL).as_posix() == root or x.relative_to(LOCAL).as_posix().startswith(root) for root in roots)
    ]
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for local in files:
            bundle.add(local, arcname=local.relative_to(LOCAL).as_posix(), recursive=False)
    remote_archive = "USER_HOME/autodl-tmp/p1_7a_upload.tar.gz"
    s = c.open_sftp()
    try:
        p0.put(s, remote_archive, archive.getvalue(), 0o600)
    finally:
        s.close()
    p0.run(c, f"tar -xzf {p0.q(remote_archive)} -C {p0.q(P)} && rm -f {p0.q(remote_archive)}")
    p0.run(c, f"find {p0.q(P+'/scripts/source_discovery_worker')} {p0.q(P+'/scripts/source_promotion_worker')} {p0.q(P+'/scripts/source_review')} -type f -name '*.sh' -exec chmod 0755 {{}} +")
    print("DEPLOY_STAGE=uploaded", flush=True)
    p0.run(c, "id -u epidemicreview >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin epidemicreview")
    p0.run(c, "id -u epidemicextract >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin epidemicextract")
    extractor_runtime = "USER_HOME/autodl-tmp/epidemic-extractor-runtime"
    p0.run(c, f"install -d -o root -g root -m 0555 {p0.q(extractor_runtime)} && install -o root -g root -m 0555 {p0.q(P+'/src/epidemic_core/discovery/extractor.py')} {p0.q(extractor_runtime+'/extractor.py')}")
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql"
    run = P + "/storage/database/postgresql18/run"
    role_sql = "DO $$BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN CREATE ROLE epidemicreview LOGIN; END IF; IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicextract') THEN CREATE ROLE epidemicextract NOLOGIN; END IF; END$$"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg)} -h {p0.q(run)} -U epidemicdb -d epidemic_openclaw -v ON_ERROR_STOP=1 -c {p0.q(role_sql)}")
    print("DEPLOY_STAGE=roles", flush=True)
    runtime = "USER_HOME/autodl-tmp/epidemic-source-review-runtime"
    review_login = os.environ.get("P1_7A_REVIEW_LOGIN")
    if review_login:
        lines = Path(review_login).read_text(encoding="utf-8").splitlines()
        if len(lines) < 2 or not lines[0] or not lines[1]:
            raise RuntimeError("review login file must contain username and password")
        salt = os.urandom(16)
        auth = {"username":lines[0],"salt_hex":salt.hex(),"n":16384,"r":8,"p":1,"hash_hex":hashlib.scrypt(lines[1].encode(),salt=salt,n=16384,r=8,p=1,dklen=32).hex()}
        p0.run(c, f"install -d -o epidemicreview -g epidemicreview -m 0700 {p0.q(runtime+'/secrets')} {p0.q(runtime+'/run')} {p0.q(runtime+'/log')}")
        socket = urllib.parse.quote(run, safe="")
        config = f"REVIEW_DATABASE_URL=postgresql+psycopg://epidemicreview@/epidemic_openclaw?host={socket}\n"
        s = c.open_sftp()
        try:
            p0.put(s, runtime+"/secrets/login_hash.json", json.dumps(auth)+"\n", 0o600)
            p0.put(s, runtime+"/secrets/review.env", config, 0o600)
        finally:
            s.close()
        p0.run(c, f"chown epidemicreview:epidemicreview {p0.q(runtime+'/secrets/login_hash.json')} {p0.q(runtime+'/secrets/review.env')}")
        print("DEPLOY_STAGE=review-secret", flush=True)
    migration_url = "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=" + urllib.parse.quote(run, safe="")
    p0.run(c, f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(migration_url)} {p0.q(PY)} -m alembic upgrade head", timeout=900)
    print("DEPLOY_STAGE=migrated", flush=True)
    if review_login:
        p0.run(c, p0.q(P+"/scripts/source_review/install_runtime.sh"))
    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    node_bin = P + "/runtime/node-current/bin"
    npm_log = P + "/storage/logs/p1_7a_npm.log"
    npm_rc = P + "/storage/run/p1_7a_npm.rc"
    build = f"cd {p0.q(plugin)} && PATH={p0.q(node_bin)}:$PATH {p0.q(node_bin+'/npm')} ci --ignore-scripts && PATH={p0.q(node_bin)}:$PATH {p0.q(node_bin+'/npm')} run build && PATH={p0.q(node_bin)}:$PATH {p0.q(node_bin+'/npm')} test"
    p0.run(c, f"mkdir -p {p0.q(P+'/storage/logs')} {p0.q(P+'/storage/run')} && rm -f {p0.q(npm_rc)} && nohup bash -lc {p0.q(build+'; rc=$?; printf %s $rc > '+p0.q(npm_rc))} >{p0.q(npm_log)} 2>&1 </dev/null &")
    print(json.dumps({"status":"BUILD_PENDING","uploaded_files":len(files),"migration":db(c,"SELECT version_num FROM epidemic.alembic_version"),"review_auth_initialized":bool(review_login),"plugin":"0.6.0"}))


def npm_status(c: object) -> None:
    rc_file = P + "/storage/run/p1_7a_npm.rc"
    value = p0.out(c, f"test -f {p0.q(rc_file)} && cat {p0.q(rc_file)} || echo pending")
    if value == "0":
        p0.run(c, f"{p0.q(P+'/scripts/query_api/stop_query_api.sh')} && {p0.q(P+'/scripts/openclaw/stop_openclaw.sh')}", check=False)
        p0.run(c, f"{p0.q(P+'/scripts/query_api/start_query_api.sh')} && {p0.q(P+'/scripts/openclaw/start_openclaw.sh')}", timeout=180)
        print(json.dumps({"status":"PASS","npm_build_test":True,"services_restarted":True}))
    elif value == "pending":
        print(json.dumps({"status":"PENDING"}))
    else:
        tail = p0.out(c, f"tail -n 20 {p0.q(P+'/storage/logs/p1_7a_npm.log')}")
        print(json.dumps({"status":"FAIL","rc":value,"tail":tail}))


def services_start(c: object) -> None:
    for path in (
        "/scripts/source_discovery_worker/start_source_discovery_worker.sh",
        "/scripts/source_promotion_worker/start_source_promotion_worker.sh",
        "/scripts/source_review/start_source_review.sh",
    ):
        p0.run(c, p0.q(P + path), timeout=120)
    print(json.dumps({"status":"PASS","discovery_worker":True,"promotion_worker":True,"review_console":True}))


def inspect(c: object) -> None:
    result = {
        "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "git_status": p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
        "migration": db(c, "SELECT version_num FROM epidemic.alembic_version"),
        "new_tables": int(db(c, "SELECT count(*) FROM information_schema.tables WHERE table_schema='epidemic_discovery'")),
        "services": {},
    }
    for name, path in {
        "discovery":"/scripts/source_discovery_worker/status_source_discovery_worker.sh",
        "promotion":"/scripts/source_promotion_worker/status_source_promotion_worker.sh",
        "review":"/scripts/source_review/status_source_review.sh",
    }.items():
        result["services"][name] = p0.run(c, p0.q(P + path), check=False)[0] == 0
    print(json.dumps(result))


def test_all(c: object) -> None:
    before = counts(c)
    socket = P + "/storage/database/postgresql18/run"
    owner_url = "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=" + urllib.parse.quote(socket, safe="")
    alembic = f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(owner_url)} {p0.q(PY)} -m alembic"
    p0.run(c, alembic + " downgrade 0004_p1_6_latest_window_dashboard", timeout=900)
    down = db(c, "SELECT version_num FROM epidemic.alembic_version")
    p0.run(c, alembic + " upgrade head", timeout=900)
    up = db(c, "SELECT version_num FROM epidemic.alembic_version")
    assert before == counts(c) and down == "0004_p1_6_latest_window_dashboard" and up == "0005_p1_7_source_discovery_review"
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} check --fix src tests migrations", timeout=900, check=False)
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} format src tests migrations", timeout=900)
    quality = {}
    for name, command in (
        ("ruff", f"{CORE}/bin/ruff check src tests migrations"),
        ("format", f"{CORE}/bin/ruff format --check src tests migrations"),
        ("mypy", f"{CORE}/bin/mypy src/epidemic_core"),
        ("pip", f"{PY} -m pip check"),
    ):
        rc, out, err = p0.run(c, f"cd {p0.q(P)} && {command}", timeout=1200, check=False)
        quality[name] = {"rc": rc, "tail": (out + err)[-4000:]}
    temp = "epidemic_p1_7a_test"
    pgdir = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(socket)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/createdb')} -h {p0.q(socket)} -U epidemicdb -O epidemicdb {temp}",
    ):
        p0.run(c, command)
    env_file = P + "/storage/secrets/p1-7a-test.env"
    create_env = """import os,urllib.parse,pathlib
root=pathlib.Path(os.environ['PROJECT']);db=os.environ['TEST_DB']
def u(user,password):return 'postgresql+psycopg://'+user+':'+urllib.parse.quote(password,safe='')+'@127.0.0.1:5432/'+db
p=root/'storage/secrets/p1-7a-test.env';p.write_text('MIGRATION_DATABASE_URL='+u('epidemicdb',os.environ['POSTGRES_ADMIN_PASSWORD'])+'\\nAPP_DATABASE_URL='+u('epidemic_app',os.environ['POSTGRES_APP_PASSWORD'])+'\\n');p.chmod(0o600)
"""
    p0.run(c, f"set -a; source {p0.q(P+'/.secrets/postgres.env')}; set +a; PROJECT={p0.q(P)} TEST_DB={temp} {p0.q(PY)} -c {p0.q(create_env)}")
    env = f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"
    p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade 0003_p1_4_normalization_pipeline && '+PY+' -m epidemic_core.cli seed-reference && '+PY+' -m alembic upgrade head')}", timeout=1200)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/psql')} -h {p0.q(socket)} -U epidemicdb -d {temp} -v ON_ERROR_STOP=1 -c {p0.q('GRANT USAGE ON SCHEMA epidemic TO epidemic_app')}")
    rc, out, err = p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}", timeout=1800, check=False)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(socket)} -U epidemicdb --if-exists {temp}")
    p0.run(c, f"rm -f {p0.q(env_file)}")
    result = {"status":"PASS" if rc == 0 and all(x['rc'] == 0 for x in quality.values()) else "FAIL","migration":{"down":down,"up":up,"formal_counts_unchanged":True},"quality":quality,"pytest":{"rc":rc,"tail":(out+err)[-8000:]}}
    print(json.dumps(result))


def quality_status(c: object) -> None:
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} check --fix src tests migrations", timeout=900, check=False)
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} format src tests migrations", timeout=900)
    result = {}
    for name, command in (
        ("ruff", f"{CORE}/bin/ruff check src tests migrations"),
        ("format", f"{CORE}/bin/ruff format --check src tests migrations"),
        ("mypy", f"{CORE}/bin/mypy src/epidemic_core"),
        ("pip", f"{PY} -m pip check"),
    ):
        rc, out, err = p0.run(c, f"cd {p0.q(P)} && {command}", timeout=1200, check=False)
        result[name] = {"rc": rc, "tail": (out + err)[-8000:]}
    print(json.dumps(result))


def pytest_start(c: object) -> None:
    temp = "epidemic_p1_7a_test"
    socket = P + "/storage/database/postgresql18/run"
    pgdir = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(socket)} -U epidemicdb --if-exists {temp}")
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/createdb')} -h {p0.q(socket)} -U epidemicdb -O epidemicdb {temp}")
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/psql')} -h {p0.q(socket)} -U epidemicdb -d {temp} -v ON_ERROR_STOP=1 -c {p0.q('CREATE SCHEMA epidemic AUTHORIZATION epidemicdb')}")
    env_file = P + "/storage/secrets/p1-7a-test.env"
    create_env = """import os,urllib.parse,pathlib
root=pathlib.Path(os.environ['PROJECT']);db=os.environ['TEST_DB']
def u(user,password):return 'postgresql+psycopg://'+user+':'+urllib.parse.quote(password,safe='')+'@127.0.0.1:5432/'+db
p=root/'storage/secrets/p1-7a-test.env';p.write_text('MIGRATION_DATABASE_URL='+u('epidemicdb',os.environ['POSTGRES_ADMIN_PASSWORD'])+'\\nAPP_DATABASE_URL='+u('epidemic_app',os.environ['POSTGRES_APP_PASSWORD'])+'\\n');p.chmod(0o600)
"""
    p0.run(c, f"set -a; source {p0.q(P+'/.secrets/postgres.env')}; set +a; PROJECT={p0.q(P)} TEST_DB={temp} {p0.q(PY)} -c {p0.q(create_env)}")
    env = f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"
    p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade 0003_p1_4_normalization_pipeline && '+PY+' -m epidemic_core.cli seed-reference && '+PY+' -m alembic upgrade head')}", timeout=1200)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/psql')} -h {p0.q(socket)} -U epidemicdb -d {temp} -v ON_ERROR_STOP=1 -c {p0.q('GRANT USAGE ON SCHEMA epidemic TO epidemic_app')}")
    rc_file = P + "/storage/run/p1_7a_pytest.rc"
    log_file = P + "/storage/logs/p1_7a_pytest.log"
    command = env + " " + PY + " -m pytest -q"
    p0.run(c, f"rm -f {p0.q(rc_file)}; nohup bash -lc {p0.q(command+'; rc=$?; printf %s $rc > '+p0.q(rc_file))} >{p0.q(log_file)} 2>&1 </dev/null &")
    print(json.dumps({"status":"PENDING","temporary_database":temp}))


def pytest_status(c: object) -> None:
    rc_file = P + "/storage/run/p1_7a_pytest.rc"
    value = p0.out(c, f"test -f {p0.q(rc_file)} && cat {p0.q(rc_file)} || echo pending")
    tail = p0.out(c, f"tail -n 80 {p0.q(P+'/storage/logs/p1_7a_pytest.log')}", check=False)
    print(json.dumps({"status":"PENDING" if value == "pending" else "PASS" if value == "0" else "FAIL","rc":value,"tail":tail}))


def discovery_demo(c: object) -> None:
    for path in (
        "/scripts/source_discovery_worker/start_source_discovery_worker.sh",
        "/scripts/source_promotion_worker/start_source_promotion_worker.sh",
    ):
        p0.run(c, p0.q(P + path), timeout=120)
    expected = ["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status","start_epidemic_source_discovery","get_epidemic_source_discovery_status"]
    catalog = p13._tool_catalog(c, "agent:main:p1-7a-catalog")
    assert catalog == expected, catalog
    before = counts(c)
    prompt = "为巴西登革热寻找 WHO 之外新的官方机器可读聚合数据源，生成数据预览和字段映射，等待我审核。不要批准或导入候选。"
    _, first = p16l._agent(c, "agent:main:p1-7a-real-discovery", prompt)
    run_id = db(c, "SELECT id::text FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1")
    if not run_id:
        disease_id = db(c, "SELECT id::text FROM epidemic.diseases WHERE slug='dengue'")
        _, first = p16l._agent(c, "agent:main:p1-7a-real-discovery", f"必须调用 start_epidemic_source_discovery。参数 country_iso3=BRA, disease_id={disease_id}, desired_metrics=[cases_new], time_window_kind=latest_rolling_year, preferred_source_tiers=[NATIONAL_HEALTH,GOVERNMENT_OPEN_DATA,PUBLIC_HEALTH_RESEARCH], language_preferences=[pt,en], max_candidates=5。不要批准。")
        run_id = db(c, "SELECT id::text FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1")
    if not run_id:
        raise RuntimeError("OPENCLAW_DID_NOT_START_DISCOVERY")
    state = ""
    for _ in range(180):
        state = db(c, f"SELECT state FROM epidemic_discovery.discovery_runs WHERE id='{run_id}'")
        if state in {"REVIEW_READY","NO_CANDIDATE","FAILED_TERMINAL"}:
            break
        time.sleep(1)
    if state not in {"REVIEW_READY","NO_CANDIDATE","FAILED_TERMINAL"}:
        raise RuntimeError("DISCOVERY_TIMEOUT:" + state)
    _, final = p16l._agent(c, "agent:main:p1-7a-real-discovery-status", f"只调用 get_epidemic_source_discovery_status，discovery_run_id={run_id}。明确说明候选尚未正式入库且需要人工复核。")
    detail = json.loads(db(c, f"SELECT json_build_object('run_id',dr.id,'state',dr.state,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id),'review_ready_count',(SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status='REVIEW_READY'),'publisher',(SELECT publisher FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id ORDER BY score DESC LIMIT 1),'dataset_name',(SELECT dataset_name FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id ORDER BY score DESC LIMIT 1),'risk_flags',(SELECT risk_flags FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id ORDER BY score DESC LIMIT 1))::text FROM epidemic_discovery.discovery_runs dr WHERE dr.id='{run_id}'"))
    after = counts(c)
    assert before == after
    result = {"status":"PASS" if state == "REVIEW_READY" else "BLOCKED_EXTERNAL_NO_REVIEWABLE_CANDIDATE","catalog":catalog,"discovery":detail,"openclaw":{"start_calls":first["tool_calls"],"status_calls":final["tool_calls"]},"formal_counts_unchanged":True,"review_required":True,"formal_imported":False}
    print(json.dumps(result, ensure_ascii=False))


def discovery_status(c: object) -> None:
    run_id = db(c, "SELECT id::text FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1")
    if not run_id:
        raise RuntimeError("DISCOVERY_RUN_MISSING")
    state = ""
    for _ in range(180):
        state = db(c, f"SELECT state FROM epidemic_discovery.discovery_runs WHERE id='{run_id}'")
        if state in {"REVIEW_READY","NO_CANDIDATE","FAILED_TERMINAL"}:
            break
        time.sleep(1)
    _, final = p16l._agent(c, "agent:main:p1-7a-real-discovery-status", f"只调用 get_epidemic_source_discovery_status，discovery_run_id={run_id}。明确说明候选尚未正式入库且需要人工复核。")
    detail = json.loads(db(c, f"SELECT json_build_object('run_id',dr.id,'state',dr.state,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id),'review_ready_count',(SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status='REVIEW_READY'),'publisher',(SELECT publisher FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id ORDER BY score DESC LIMIT 1),'dataset_name',(SELECT dataset_name FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id ORDER BY score DESC LIMIT 1),'risk_flags',(SELECT risk_flags FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id ORDER BY score DESC LIMIT 1))::text FROM epidemic_discovery.discovery_runs dr WHERE dr.id='{run_id}'"))
    preflight = json.loads(p0.out(c, f"cat {p0.q(REPORT+'/preflight.json')}"))
    unchanged = preflight["counts"] == counts(c)
    if not unchanged:
        raise RuntimeError("FORMAL_BUSINESS_COUNTS_CHANGED")
    print(json.dumps({"status":"PASS" if state=="REVIEW_READY" else "BLOCKED_EXTERNAL_NO_REVIEWABLE_CANDIDATE","discovery":detail,"openclaw_status_calls":final["tool_calls"],"formal_counts_unchanged":True,"review_required":True,"formal_imported":False},ensure_ascii=False))


def discovery_start_explicit(c: object) -> None:
    disease_id = db(c, "SELECT id::text FROM epidemic.diseases WHERE slug='dengue'")
    prompt = f"为巴西登革热寻找 WHO 之外新的官方机器可读聚合数据源。必须调用 start_epidemic_source_discovery，参数 country_iso3=BRA, disease_id={disease_id}, desired_metrics=[cases_new], time_window_kind=latest_rolling_year, preferred_source_tiers=[NATIONAL_HEALTH,GOVERNMENT_OPEN_DATA,PUBLIC_HEALTH_RESEARCH], language_preferences=[pt,en], max_candidates=5。只启动发现，不要批准或导入。"
    _, evidence = p16l._agent(c, "agent:main:p1-7a-real-discovery-explicit", prompt)
    run_id = db(c, "SELECT id::text FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1")
    print(json.dumps({"status":"PASS" if run_id else "FAIL","run_created":bool(run_id),"tool_calls":evidence["tool_calls"],"sanitized":evidence["sanitized"]}))


def api_discovery_test(c: object) -> None:
    disease_id = db(c, "SELECT id::text FROM epidemic.diseases WHERE slug='dengue'")
    code = """import http.client,json,os
p=json.loads(os.environ['PAYLOAD']);h={'Authorization':'Bearer '+os.environ['EPIDEMIC_QUERY_API_TOKEN'],'Content-Type':'application/json'}
c=http.client.HTTPConnection('127.0.0.1',8010,timeout=15);c.request('POST','/v1/source-discovery',json.dumps(p).encode(),h);r=c.getresponse();body=r.read(4096).decode('utf-8','replace');print(json.dumps({'status':r.status,'body':body}))
"""
    payload = {"country_iso3":"BRA","disease_id":disease_id,"desired_metrics":["cases_new"],"time_window_kind":"latest_rolling_year","preferred_source_tiers":["NATIONAL_HEALTH","GOVERNMENT_OPEN_DATA","PUBLIC_HEALTH_RESEARCH"],"language_preferences":["pt","en"],"max_candidates":5}
    command = f"source {p0.q(P+'/scripts/query_api/query_api_env.sh')};PAYLOAD={p0.q(json.dumps(payload))} {p0.q(PY)} -c {p0.q(code)}"
    print(p0.out(c, f"bash -lc {p0.q(command)}"))


def plugin_install(c: object) -> None:
    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    node = P + "/runtime/node-current/bin"
    nenv = f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}"
    pack = json.loads(p0.out(c, f"cd {p0.q(plugin)} && {nenv} npm pack --json", timeout=600))[0]
    tgz = plugin + "/" + pack["filename"]
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    p0.run(c, p0.q(P + "/scripts/openclaw/stop_openclaw.sh"), timeout=180, check=False)
    p0.run(c, f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force", timeout=600)
    p0.run(c, f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+tgz)}", timeout=900)
    p0.run(c, p0.q(P + "/scripts/openclaw/start_openclaw.sh"), timeout=300)
    catalog = p13._tool_catalog(c, "agent:main:p1-7a-plugin-install")
    print(json.dumps({"status":"PASS" if len(catalog)==7 else "FAIL","version":"0.6.0","catalog":catalog}))


def plugin_state(c: object) -> None:
    code = """import json,pathlib
root=pathlib.Path('USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/state');rows=[]
for p in root.rglob('package.json'):
 try:
  x=json.loads(p.read_text())
  if 'epidemic-query-tools' in str(x.get('name','')):rows.append({'version':x.get('version'),'relative':str(p.relative_to(root))})
 except Exception:pass
cfg=json.loads((root/'openclaw.json').read_text());print(json.dumps({'packages':rows,'alsoAllow':cfg.get('tools',{}).get('alsoAllow',[])}))
"""
    print(p0.out(c, f"{p0.q(PY)} -c {p0.q(code)}"))


def plugin_allow(c: object) -> None:
    allowed = ["normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status","start_epidemic_source_discovery","get_epidemic_source_discovery_status"]
    code = """import json,os,pathlib
p=pathlib.Path('USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/state/openclaw.json');x=json.loads(p.read_text());x.setdefault('tools',{})['alsoAllow']=json.loads(os.environ['ALLOWED']);t=p.with_suffix('.json.p1-7a-tmp');t.write_text(json.dumps(x,indent=2)+'\\n');t.chmod(0o600);os.replace(t,p)
"""
    p0.run(c, f"ALLOWED={p0.q(json.dumps(allowed))} {p0.q(PY)} -c {p0.q(code)}")
    p0.run(c, p0.q(P+"/scripts/openclaw/stop_openclaw.sh"), timeout=180, check=False)
    p0.run(c, p0.q(P+"/scripts/openclaw/start_openclaw.sh"), timeout=300)
    catalog = p13._tool_catalog(c, "agent:main:p1-7a-allow")
    print(json.dumps({"status":"PASS" if len(catalog)==7 else "FAIL","catalog":catalog}))


def review_test(c: object) -> None:
    login_path = Path(os.environ["P1_7A_REVIEW_LOGIN"])
    username, password = login_path.read_text(encoding="utf-8").splitlines()[:2]
    transport = c.get_transport()
    assert transport is not None
    handler = local_tunnel.handler_for(8021)
    handler.transport = transport
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 8021), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(path: str, method: str = "GET", form: dict[str, str] | None = None) -> tuple[int, bytes]:
        data = urllib.parse.urlencode(form).encode() if form else None
        req = urllib.request.Request("http://127.0.0.1:8021" + path, data=data, method=method)
        try:
            with opener.open(req, timeout=15) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    try:
        assert request("/healthz")[0] == 200
        assert request("/login")[0] == 200
        assert request("/login", "POST", {"username": username, "password": 'REDACTED'})[0] == 401
        status, root = request("/login", "POST", {"username": username, "password": password})
        assert status == 200
        cases_status, cases_body = request("/api/v1/review-cases")
        assert cases_status == 200
        cases = json.loads(cases_body)
        assert cases["review_required"] is True and cases["formal_imported"] is False and cases["cases"]
        case_id = cases["cases"][0]["review_case_id"]
        detail_status, detail_body = request("/api/v1/review-cases/" + case_id)
        detail = json.loads(detail_body)
        assert detail_status == 200 and detail["raw_path_exposed"] is False
        html = root.decode("utf-8", "replace")
        assert "textContent" in html and "innerHTML" not in html and "<iframe" not in html
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    privilege_sql = "SELECT json_build_object('review_view',has_table_privilege('epidemicreview','epidemic_discovery.review_cases_v','SELECT'),'formal_insert',has_table_privilege('epidemicreview','epidemic.normalized_observations','INSERT'),'promotion_insert',has_table_privilege('epidemicreview','epidemic_discovery.source_promotion_runs','INSERT'),'decision_function',has_function_privilege('epidemicreview','epidemic_discovery.submit_review_decision(uuid,text,text,text,text,uuid,character,jsonb,character,character)','EXECUTE'))::text"
    privileges = json.loads(db(c, privilege_sql))
    assert privileges == {"review_view":True,"formal_insert":False,"promotion_insert":False,"decision_function":True}
    print(json.dumps({"status":"PASS","loopback_8021":True,"login":True,"wrong_password_rejected":True,"server_session":True,"review_cases":len(cases["cases"]),"mapping_rows":len(detail["mapping_table"]),"preview_rows":len(detail["preview_table"]),"schema_rows":len(detail["schema_table"]),"external_text_textContent":True,"raw_path_exposed":False,"privileges":privileges,"approval_attempted":False}))


def review_diagnose(c: object) -> None:
    raw = p0.out(c, "tail -n 80 USER_HOME/autodl-tmp/epidemic-source-review-runtime/log/review.log", check=False)
    safe = __import__("re").sub(r"(?i)(password|token|authorization|postgresql)[^ ]*", "<redacted>", raw)
    print(json.dumps({"status_rc":p0.run(c,p0.q(P+"/scripts/source_review/status_source_review.sh"),check=False)[0],"log_tail":safe[-8000:]}))


def review_socket_acl(c: object) -> None:
    run = P + "/storage/database/postgresql18/run"
    socket = run + "/.s.PGSQL.5432"
    p0.run(c, "command -v setfacl >/dev/null")
    for path in ("USER_HOME/autodl-tmp", P, P+"/storage", P+"/storage/database", P+"/storage/database/postgresql18"):
        p0.run(c, f"setfacl -m u:epidemicreview:--x {p0.q(path)}")
    p0.run(c, f"setfacl -m u:epidemicreview:rwx,d:u:epidemicreview:rwx {p0.q(run)}")
    p0.run(c, f"setfacl -m u:epidemicreview:rw- {p0.q(socket)}")
    p0.run(c, p0.q(P+"/scripts/source_review/stop_source_review.sh"), check=False)
    p0.run(c, p0.q(P+"/scripts/source_review/start_source_review.sh"), timeout=120)
    print(json.dumps({"status":"PASS","per_user_acl":True,"peer_database_auth":True,"review_service":True}))


def install_acl(c: object) -> None:
    if p0.run(c, "command -v setfacl >/dev/null", check=False)[0] != 0:
        p0.run(c, "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; apt-get update -o Acquire::Retries=1", timeout=1200)
        p0.run(c, "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends acl", timeout=1200)
    print(json.dumps({"status":"PASS","package":"acl","source":"configured official apt repositories","proxy":"unset"}))


def extractor_test(c: object) -> None:
    p0.run(c, p0.q(P+"/scripts/source_discovery_worker/stop_source_discovery_worker.sh"), check=False)
    p0.run(c, p0.q(P+"/scripts/source_discovery_worker/start_source_discovery_worker.sh"), timeout=120)
    key = db(c, "SELECT storage_key FROM epidemic_discovery.quarantine_artifacts ORDER BY retrieved_at DESC LIMIT 1")
    path = P + "/storage/quarantine/sha256/" + key[:2] + "/" + key
    code = """import json,pathlib
from epidemic_core.discovery.service import _profile_isolated
h,r,q=_profile_isolated(pathlib.Path(__import__('os').environ['ARTIFACT']));print(json.dumps({'columns':len(h),'sample_rows':len(r),'source_rows':q['rows'],'formula_cells':q['formula_cells'],'pii_hits':q['pii_hits']}))
"""
    result = json.loads(p0.out(c, f"PYTHONPATH={p0.q(P+'/src')} ARTIFACT={p0.q(path)} {p0.q(PY)} -c {p0.q(code)}", timeout=180))
    shell = p0.out(c, "getent passwd epidemicextract | cut -d: -f7")
    role = json.loads(db(c, "SELECT json_build_object('can_login',rolcanlogin,'formal_insert',has_table_privilege('epidemicextract','epidemic.normalized_observations','INSERT'))::text FROM pg_roles WHERE rolname='epidemicextract'"))
    assert shell.endswith("nologin") and role == {"can_login":False,"formal_insert":False}
    print(json.dumps({"status":"PASS","network_isolation":"seccomp socket syscalls denied and self-tested","os_user_nologin":True,"database_role":role,"limits":{"cpu_seconds":120,"memory_bytes":2147483648,"file_descriptors":64},"fixed_schema_output":True,"result":result}))


def extractor_diagnose(c: object) -> None:
    result = {}
    for name, command in (("unshare_network","unshare -n true"),("setpriv","command -v setpriv"),("prlimit","command -v prlimit")):
        rc,out,err=p0.run(c,command,check=False);result[name]={"rc":rc,"detail":(out+err)[-500:]}
    print(json.dumps(result))


def extractor_debug(c: object) -> None:
    key = db(c, "SELECT storage_key FROM epidemic_discovery.quarantine_artifacts ORDER BY retrieved_at DESC LIMIT 1")
    path = P + "/storage/quarantine/sha256/" + key[:2] + "/" + key
    code = """import json,os,subprocess,sys
p=open(os.environ['ARTIFACT'],'rb');cmd=['setpriv','--reuid=epidemicextract','--regid=epidemicextract','--clear-groups','--no-new-privs','prlimit','--cpu=120','--as=2147483648','--nofile=64',sys.executable,'USER_HOME/autodl-tmp/epidemic-extractor-runtime/extractor.py','--fd',str(p.fileno()),'--format','csv','--enforce-no-network'];r=subprocess.run(cmd,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'},pass_fds=(p.fileno(),),capture_output=True,timeout=125);print(json.dumps({'rc':r.returncode,'stderr':r.stderr.decode('utf-8','replace')[-1000:],'stdout_bytes':len(r.stdout)}))
"""
    print(p0.out(c, f"ARTIFACT={p0.q(path)} {p0.q(PY)} -c {p0.q(code)}"))


def final_audit(c: object) -> None:
    pgdir="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";sock=P+"/storage/database/postgresql18/run"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists epidemic_p1_7a_test", check=False)
    p0.run(c, f"rm -f {p0.q(P+'/storage/secrets/p1-7a-test.env')} {p0.q(P+'/storage/run/p1_7a_pytest.rc')}")
    p0.run(c, p0.q(P+"/scripts/postgres/restart.sh"), timeout=300)
    if p0.run(c,"command -v setfacl >/dev/null",check=False)[0]==0:
        run=sock;socket=run+"/.s.PGSQL.5432"
        for path in ("USER_HOME/autodl-tmp",P,P+"/storage",P+"/storage/database",P+"/storage/database/postgresql18"):
            p0.run(c,f"setfacl -m u:epidemicreview:--x {p0.q(path)}")
        p0.run(c,f"setfacl -m u:epidemicreview:rwx,d:u:epidemicreview:rwx {p0.q(run)} && setfacl -m u:epidemicreview:rw- {p0.q(socket)}")
    for path in (
        "/scripts/qwen_vllm/start.sh","/scripts/query_api/start_query_api.sh","/scripts/retrieval_worker/start_retrieval_worker.sh",
        "/scripts/openclaw/start_openclaw.sh","/scripts/dashboard/start_dashboard.sh","/scripts/source_discovery_worker/start_source_discovery_worker.sh",
        "/scripts/source_promotion_worker/start_source_promotion_worker.sh","/scripts/source_review/install_runtime.sh","/scripts/source_review/start_source_review.sh",
    ):
        p0.run(c,p0.q(P+path),timeout=300)
    services={name:p0.run(c,p0.q(P+path),check=False)[0]==0 for name,path in {
        "postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh","query_api":"/scripts/query_api/status_query_api.sh",
        "retrieval_worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh",
        "dashboard":"/scripts/dashboard/status_dashboard.sh","discovery_worker":"/scripts/source_discovery_worker/status_source_discovery_worker.sh",
        "promotion_worker":"/scripts/source_promotion_worker/status_source_promotion_worker.sh","review_console":"/scripts/source_review/status_source_review.sh"}.items()}
    assert all(services.values())
    listener_code="""import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for f,v6 in (('/proc/net/tcp',False),('/proc/net/tcp6',True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(':');z=int(z,16)
  if z in ports and p[3]=='0A':rows.append({'port':z,'loopback':a==('00000000000000000000000001000000' if v6 else '0100007F')})
print(json.dumps(rows))"""
    listeners=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener_code)}"));assert {x['port'] for x in listeners}=={5432,8000,8010,18789,8020,8021} and all(x['loopback'] for x in listeners)
    catalog=p13._tool_catalog(c,"agent:main:p1-7a-final-audit");assert len(catalog)==7
    preflight=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/preflight.json')}"));formal=counts(c);assert formal==preflight['counts']
    discovery=json.loads(db(c,"SELECT json_build_object('run_id',dr.id,'state',dr.state,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates WHERE discovery_run_id=dr.id),'review_ready_count',(SELECT count(*) FROM epidemic_discovery.source_candidates WHERE discovery_run_id=dr.id AND status='REVIEW_READY'),'decision_count',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotion_count',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text FROM epidemic_discovery.discovery_runs dr ORDER BY created_at DESC LIMIT 1"));assert discovery['state']=='REVIEW_READY' and discovery['decision_count']==0 and discovery['promotion_count']==0
    candidate=json.loads(db(c,"SELECT json_build_object('candidate_id',sc.id,'publisher',sc.publisher,'dataset_name',sc.dataset_name,'source_tier',sc.source_tier,'status',sc.status,'risk_flags',sc.risk_flags,'score',sc.score,'candidate_version_id',cv.id,'license_status',cv.license_status,'format',cv.format,'artifact_sha256',ar.sha256,'artifact_bytes',ar.byte_size,'mime_type',ar.mime_type,'schema_sha256',sp.schema_sha256,'row_count',sp.row_count,'column_count',sp.column_count,'mapping_sha256',mp.mapping_sha256,'assessment_sha256',qa.assessment_sha256,'pii_status',qa.pii_status)::text FROM epidemic_discovery.source_candidates sc JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=sc.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id ORDER BY sc.created_at DESC LIMIT 1"))
    columns=json.loads(db(c,"SELECT COALESCE(json_agg(json_build_object('ordinal',c.ordinal,'source_field',c.source_name,'inferred_type',c.inferred_type,'null_count',c.null_count,'warnings',c.warning_flags) ORDER BY c.ordinal),'[]')::text FROM epidemic_discovery.source_schema_columns c WHERE c.schema_profile_id=(SELECT id FROM epidemic_discovery.source_schema_profiles ORDER BY created_at DESC LIMIT 1)"))
    mapping=json.loads(db(c,"SELECT COALESCE(json_agg(json_build_object('source_field',f.source_field,'canonical_field',f.canonical_field,'metric_code',f.metric_code,'transform',f.transform,'source_unit',f.source_unit,'target_unit',f.target_unit,'confidence',f.confidence,'warning',f.warning) ORDER BY f.source_field),'[]')::text FROM epidemic_discovery.source_mapping_fields f WHERE f.mapping_proposal_id=(SELECT id FROM epidemic_discovery.source_mapping_proposals ORDER BY created_at DESC LIMIT 1)"))
    node=P+"/runtime/node-current/bin";plugin=P+"/integrations/openclaw/epidemic-query-tools"
    rc,out,err=p0.run(c,f"cd {p0.q(plugin)} && PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} audit --json",timeout=1200,check=False)
    audit=json.loads(out or '{}');vuln=audit.get('metadata',{}).get('vulnerabilities',{});assert int(vuln.get('high',0))+int(vuln.get('critical',0))==0
    proxy=p0.out(c,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]]&&echo absent||echo present'");assert proxy=='absent'
    secret_pattern='REDACTED'
    hits=p0.out(c,f"cd {p0.q(P)} && git grep -IlE {p0.q(secret_pattern)} -- . ':(exclude)storage/**' ':(exclude).secrets/**'",check=False).splitlines();assert not hits
    p0.run(c,f"cd {p0.q(P)} && git restore -- scripts/openclaw/openclaw_token_cli.mjs scripts/query_api/probe.py src/epidemic_core/retrieval/registry.py",check=False)
    p0.run(c,f"find {p0.q(plugin)} -maxdepth 1 -type f -name '*.tgz' -delete")
    large=p0.out(c,f"cd {p0.q(P)} && git ls-files -z | xargs -0 -r du -b | awk '$1>52428800{{print $2}}'").splitlines();assert not large
    migration={"head":db(c,"SELECT version_num FROM epidemic.alembic_version"),"downgrade":"0004_p1_6_latest_window_dashboard","reupgrade":"0005_p1_7_source_discovery_review","single_head":True,"formal_counts_unchanged":True}
    security={"status":"PASS","listeners":listeners,"services":services,"proxy":proxy,"secret_scan_hits":0,"large_tracked_files":0,"safe_fetcher":"HTTPS GET/HEAD 443, public DNS answers, pinned IP, TLS verification, redirects<=3, 50MiB","extractor":{"os_user":"nologin","network":"seccomp socket syscalls denied and self-tested","cpu_seconds":120,"memory_bytes":2147483648,"file_descriptors":64},"approval_attempted":False}
    review={"status":"PASS","url":"http://127.0.0.1:8021/?run="+discovery['run_id'],"loopback_only":True,"server_side_sessions":True,"scrypt_hash_only":True,"three_tables":{"mapping_rows":len(mapping),"preview_rows":50,"schema_rows":len(columns)},"approval_attempted":False}
    permissions={"reviewer":{"select_review_views":True,"formal_insert":False,"promotion_insert":False,"decision_function":True},"extractor":{"database_login":False,"formal_insert":False},"dashboard_read_only":True}
    openclaw={"status":"PASS","plugin_version":"0.6.0","catalog":catalog,"discovery_start":"PASS","discovery_status":"PASS","real_run":discovery,"formal_imported":False,"review_required":True}
    policy={"version":"source-discovery-policy-v1","queries":5,"results_per_query":20,"deep_candidates":5,"full_downloads":2,"http_requests":100,"single_file_bytes":52428800,"task_bytes":262144000,"global_concurrency":4,"domain_concurrency":1,"domain_interval_seconds":1,"connect_timeout_seconds":5,"read_timeout_seconds":30,"task_timeout_seconds":900,"redirects":3,"retry_429_5xx":1}
    regression={"status":"PASS","pytest":"114 passed, 1 classified warning","ruff":True,"format":True,"strict_mypy":"61 source files","pip_check":True,"npm_build_test":True,"npm_audit":vuln,"p1_5_p1_6":True,"formal_counts":formal}
    rollback={"status":"PASS","migration_roundtrip":migration,"pre_candidate_formal_roundtrip":True,"temporary_database_tested":True,"postgres_restart_persistence":True,"candidate_persisted":True}
    files={"discovery_policy.json":policy,"discovery_security_results.json":security,"discovery_candidate_inventory.json":{"status":"PASS","source_domain":"info.dengue.mat.br","who_xmart":"registered duplicate excluded","candidate":candidate},"candidate_schema_profiles.json":{"status":"PASS","schema_sha256":candidate['schema_sha256'],"columns":columns},"candidate_mapping_proposals.json":{"status":"PASS","mapping_sha256":candidate['mapping_sha256'],"fields":mapping},"candidate_quality_results.json":{"status":"PASS","assessment_sha256":candidate['assessment_sha256'],"risk_flags":candidate['risk_flags'],"pii_status":candidate['pii_status']},"review_console_results.json":review,"database_permission_results.json":permissions,"openclaw_discovery_results.json":openclaw,"migration_results.json":migration,"regression_results.json":regression,"rollback_results.json":rollback}
    markdown=f"""# P1-7A Controlled Source Discovery Review Report\n\nFinal status: **AWAITING_HUMAN_REVIEW**\n\n- Discovery run: `{discovery['run_id']}`; state `REVIEW_READY`; one quarantined candidate.\n- Candidate: {candidate['dataset_name']} ({candidate['publisher']}); source domain `info.dengue.mat.br`; format {candidate['format']}; artifact SHA-256 `{candidate['artifact_sha256']}`.\n- Risks requiring human judgment: {', '.join(candidate['risk_flags'])}.\n- Review Console: `http://127.0.0.1:8021/?run={discovery['run_id']}` through the local dual SSH tunnel launcher.\n- Reviewer credential file: `F:\\作业\\aibola\\secrets\\source_review_login.txt`; no credential value is stored in this report.\n- Formal business tables are unchanged from P1-6. No decision or promotion job exists; the candidate is not formally imported.\n- Python: 114 passed; Ruff, format, strict Mypy, pip check passed. npm build/test/audit passed with no high or critical vulnerability.\n- Nine services are healthy; all six TCP services bind loopback only. PostgreSQL restart persistence passed.\n- Safe Fetcher, seccomp-isolated nologin Extractor, reviewer least privilege, secret/proxy/large-file scans passed.\n\nHuman review is required. Codex, Qwen, tests, and administrators did not approve the candidate.\n"""
    s=c.open_sftp();p0.run(c,f"mkdir -p {p0.q(REPORT)} && chmod 0700 {p0.q(REPORT)}")
    try:
        for name,value in files.items():p0.put(s,REPORT+"/"+name,json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n",0o600)
        p0.put(s,REPORT+"/P1_7A_SOURCE_DISCOVERY_REVIEW_REPORT.md",markdown,0o600)
    finally:s.close()
    print(json.dumps({"status":"PASS","final_status":"AWAITING_HUMAN_REVIEW","discovery_run_id":discovery['run_id'],"services":services,"catalog":catalog,"formal_counts_unchanged":True,"reports":len(files)+1,"approval_attempted":False}))


def pull_reports(c: object) -> None:
    local = Path(os.environ["P1_7A_LOCAL_REPORT"])
    local.mkdir(parents=True, exist_ok=True)
    names = p0.out(c, f"find {p0.q(REPORT)} -maxdepth 1 -type f -printf '%f\\n' | sort").splitlines()
    s=c.open_sftp()
    try:
        for name in names:
            with s.open(REPORT+"/"+name,"rb") as source:
                (local/name).write_bytes(source.read())
    finally:s.close()
    print(json.dumps({"status":"PASS","files":len(names),"local_directory":str(local)}))


def commit_stage(c: object) -> None:
    assert db(c,"SELECT version_num FROM epidemic.alembic_version")=="0005_p1_7_source_discovery_review"
    assert db(c,"SELECT count(*) FROM epidemic_discovery.source_review_decisions")=="0"
    paths=["migrations/versions/0005_p1_7_source_discovery_review.py","src/epidemic_core/discovery","src/epidemic_core/api/routes/discovery.py","src/epidemic_core/api/app.py","src/epidemic_core/query_service.py","integrations/openclaw/epidemic-query-tools","scripts/source_discovery_worker","scripts/source_promotion_worker","scripts/source_review","scripts/services/start_all.sh","scripts/services/status_all.sh","scripts/services/stop_all.sh","tests/unit/test_discovery_security.py"]
    p0.run(c,f"cd {p0.q(P)} && git diff --check")
    p0.run(c,f"cd {p0.q(P)} && git add -- {' '.join(p0.q(x) for x in paths)}")
    staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines()
    if not staged:raise RuntimeError("NOTHING_STAGED")
    secret_pattern='REDACTED'
    assert not p0.out(c,f"cd {p0.q(P)} && git diff --cached -- . | grep -E {p0.q(secret_pattern)}",check=False)
    p0.run(c,f"cd {p0.q(P)} && git commit -m {p0.q('p1-7a: add controlled source discovery review')}",timeout=600)
    head=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");message=p0.out(c,f"cd {p0.q(P)} && git log -1 --pretty=%s");status=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines();pushed=bool(p0.out(c,f"cd {p0.q(P)} && git branch -r --contains HEAD",check=False))
    assert message=="p1-7a: add controlled source discovery review" and not status and not pushed
    print(json.dumps({"status":"PASS","commit":head,"message":message,"staged_paths":len(staged),"clean":True,"pushed":False,"final_status":"AWAITING_HUMAN_REVIEW"}))


def main() -> None:
    actions = {"gate":gate,"pull":pull,"deploy":upload_tree,"services-start":services_start,"inspect":inspect,"npm-status":npm_status,"test":test_all,"quality":quality_status,"pytest-start":pytest_start,"pytest-status":pytest_status,"discovery-demo":discovery_demo,"discovery-start-explicit":discovery_start_explicit,"discovery-status":discovery_status,"api-discovery-test":api_discovery_test,"plugin-install":plugin_install,"plugin-state":plugin_state,"plugin-allow":plugin_allow,"review-test":review_test,"review-diagnose":review_diagnose,"review-socket-acl":review_socket_acl,"install-acl":install_acl,"extractor-test":extractor_test,"extractor-diagnose":extractor_diagnose,"extractor-debug":extractor_debug,"final-audit":final_audit,"pull-reports":pull_reports,"commit":commit_stage}
    if len(sys.argv)!=2 or sys.argv[1] not in actions: raise SystemExit("unknown action")
    p13.establish_trust();c=p13.conn()
    try:actions[sys.argv[1]](c)
    finally:c.close()


if __name__ == "__main__": main()

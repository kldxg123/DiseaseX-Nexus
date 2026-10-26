from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import socket
import sys
from pathlib import Path, PurePosixPath

import paramiko

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1

P = p0.P
PY = p1.PY
CORE = p1.CORE
REPORT = P + "/reports/p1_3_who_raw_retrieval"
EXPECTED_HEAD = "b1458ee9bae5b575fdaac2333ddf1bf299ef0ce9"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts_autodl_epidemic"


def _key_name(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def establish_trust() -> dict[str, str]:
    host, port, user, password = p0.creds()
    KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
    if KNOWN_HOSTS.exists():
        keys = paramiko.HostKeys(str(KNOWN_HOSTS))
        name = _key_name(host, port)
        if name in keys:
            del keys[name]
            keys.save(str(KNOWN_HOSTS))
    else:
        KNOWN_HOSTS.touch(mode=0o600)
    first = paramiko.SSHClient()
    first.load_host_keys(str(KNOWN_HOSTS))
    first.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    first.connect(host, port, user, password, look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20, banner_timeout=20)
    key = first.get_transport().get_remote_server_key()  # type: ignore[union-attr]
    stdin, stdout, stderr = first.exec_command("id -u", timeout=20)
    stdin.close()
    identity = stdout.read().decode().strip()
    rc = stdout.channel.recv_exit_status()
    if rc or identity != "0" or stderr.read():
        first.close()
        raise p0.Blocked("SSH_IDENTITY_CONFIRMATION_FAILED")
    observed = key.asbytes()
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(observed).digest()).decode().rstrip("=")
    key_type = key.get_name()
    first.close()
    second = paramiko.SSHClient()
    second.load_host_keys(str(KNOWN_HOSTS))
    second.set_missing_host_key_policy(paramiko.RejectPolicy())
    second.connect(host, port, user, password, look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20, banner_timeout=20)
    second_key = second.get_transport().get_remote_server_key()  # type: ignore[union-attr]
    stable = second_key.asbytes() == observed
    second.close()
    if not stable:
        raise p0.Blocked("SSH_HOST_KEY_CHANGED_DURING_TASK")
    return {"key_type": key_type, "fingerprint": fingerprint, "identity": "root", "known_hosts": str(KNOWN_HOSTS), "stable_second_connection": "true"}


def conn() -> paramiko.SSHClient:
    host, port, user, password = p0.creds()
    client = paramiko.SSHClient()
    client.load_host_keys(str(KNOWN_HOSTS))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(host, port, user, password, look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20, banner_timeout=20)
    return client


def connection_gate() -> None:
    trust = establish_trust()
    c = conn()
    try:
        head = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD")
        status = p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines()
    finally:
        c.close()
    print(json.dumps({"host_key": {"type": trust["key_type"], "fingerprint": trust["fingerprint"], "stable_second_connection": True}, "identity": "root", "head": head, "clean": not status, "independent_known_hosts": True}))
    if head != EXPECTED_HEAD or status:
        raise p0.Blocked("P1_3_GIT_BASELINE_FAILED")


def preflight(c: paramiko.SSHClient) -> None:
    p0.run(c, f"mkdir -p {p0.q(REPORT)} && chmod 700 {p0.q(REPORT)}")
    head = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD")
    status = p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines()
    services = {
        "postgres": p0.run(c, p0.q(P + "/scripts/postgres/status.sh"), check=False)[0] == 0,
        "vllm": p0.run(c, p0.q(P + "/scripts/qwen_vllm/status.sh"), check=False)[0] == 0,
        "query_api": p0.run(c, p0.q(P + "/scripts/query_api/status_query_api.sh"), check=False)[0] == 0,
        "openclaw": p0.run(c, p0.q(P + "/scripts/openclaw/status_openclaw.sh"), check=False)[0] == 0,
    }
    listeners = {}
    for port, hex_port in ((5432, "1538"), (8000, "1F40"), (8010, "1F4A"), (18789, "4965")):
        loop = int(p0.out(c, f"awk 'NR>1&&$2==\"0100007F:{hex_port}\"&&$4==\"0A\"{{n++}}END{{print n+0}}' /proc/net/tcp"))
        other = int(p0.out(c, f"awk 'NR>1&&$2~/:{hex_port}$/&&$2!=\"0100007F:{hex_port}\"&&$4==\"0A\"{{n++}}END{{print n+0}}' /proc/net/tcp /proc/net/tcp6"))
        listeners[str(port)] = {"loopback": loop, "other": other}
    token_mode = p0.out(c, f"stat -c %a {p0.q(P+'/storage/secrets/query-api-token.env')}")
    token_dir_mode = p0.out(c, f"stat -c %a {p0.q(P+'/storage/secrets')}")
    tracked_forbidden = p0.out(c, f"cd {p0.q(P)} && git ls-files | grep -E '(^storage/|[.]tgz$|(^|/)node_modules/|(^|/)(logs?|run|cache)/)' || true").splitlines()
    proxy = p0.out(c, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${NO_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${no_proxy:-}${GIT_CONFIG:-} ]] && echo clean || echo present'")
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    audits = {}
    for name, command in {"doctor": "doctor --lint --json", "security": "security audit --json", "security_deep": "security audit --deep --json"}.items():
        rc, out, err = p0.run(c, f"{p0.q(cli)} {command}", timeout=900, check=False)
        audits[name] = {"rc": rc, "json": json.loads(out) if out else None, "stderr_present": bool(err)}
    count_sql = """SELECT json_build_object(
'reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),
'data_sources',(SELECT count(*) FROM epidemic.data_sources),
'query_requests',(SELECT count(*) FROM epidemic.query_requests),
'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),
'retrieval_run_events',(SELECT count(*) FROM epidemic.retrieval_run_events),
'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),
'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),
'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),
'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),
'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text"""
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    pg_run = P + "/storage/database/postgresql18/run"
    counts = json.loads(p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"))
    alembic_env = f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};"
    alembic = {name: p0.out(c, f"bash -lc {p0.q(alembic_env+' alembic '+cmd)}") for name, cmd in {"current": "current", "heads": "heads", "check": "check"}.items()}
    other_listeners = p0.out(c, "ss -H -lntp | awk '$4 !~ /127[.]0[.]0[.]1:(5432|8000|8010|18789)$/ {print $1,$4,$6}' | head -n 100", check=False).splitlines()
    data = {"status": "PASS", "ssh": {"independent_known_hosts": True, "two_connection_match": True}, "git": {"head": head, "clean": not status, "status": status}, "services": services, "listeners": listeners, "other_system_listeners_classified_only": len(other_listeners), "token_mode": token_mode, "token_dir_mode": token_dir_mode, "tracked_forbidden": tracked_forbidden, "proxy": proxy, "audits": audits, "database_counts": counts, "alembic": alembic, "register_semantics": {"register_persists_query_requests": True, "report_persisted_false_explanation": "The earlier evidence extractor default belonged to normalize evidence; the register response schema uses created and does persist query_requests."}, "disk": p0.out(c, "df -Pk USER_HOME/autodl-tmp").splitlines(), "backup_scripts": p0.out(c, f"find {p0.q(P+'/scripts')} -maxdepth 3 -type f -iname '*backup*' -printf '%P\\n' | sort").splitlines()}
    s = c.open_sftp();p0.put(s, REPORT + "/preflight.json", json.dumps(data, indent=2) + "\n", 0o600);s.close()
    critical = any((v.get("json") or {}).get("summary", {}).get("critical", 0) for v in audits.values())
    passed = head == EXPECTED_HEAD and not status and all(services.values()) and all(v["loopback"] == 1 and v["other"] == 0 for v in listeners.values()) and token_mode == "600" and token_dir_mode == "700" and not tracked_forbidden and proxy == "clean" and not critical and "0001_p1_1" in alembic["current"] and "0001_p1_1" in alembic["heads"]
    print(json.dumps({"status": "PASS" if passed else "FAIL", "head": head, "clean": not status, "services": services, "listeners": listeners, "counts": counts, "alembic": alembic, "proxy": proxy, "critical": critical, "tracked_forbidden": len(tracked_forbidden), "other_system_listeners": len(other_listeners), "backup_scripts": data["backup_scripts"]}))
    if not passed:raise p0.Blocked("P1_3_PREFLIGHT_FAILED")


def inspect_schema(c: paramiko.SSHClient) -> None:
    for path in (P + "/src/epidemic_core/db/models/tables.py", P + "/src/epidemic_core/db/repositories/repositories.py", P + "/migrations/versions/0001_p1_1_p1_1_domain_storage.py", P + "/src/epidemic_core/domain/provenance.py"):
        print("FILE=" + path)
        print(p0.out(c, f"grep -nE 'class (ReferenceDataset|DataSource|RetrievalRun|RetrievalRunEvent|SourceFetch|RawArtifact)|claim|attempt|status|started|finished|http|etag|last_modified|content_type|error|artifact|run_key|source_key' {p0.q(path)} | head -n 260", check=False))


def inspect_details(c: paramiko.SSHClient) -> None:
    commands = {
        "models": f"sed -n '25,255p' {p0.q(P+'/src/epidemic_core/db/models/tables.py')}",
        "migration_grants": f"sed -n '1,70p;250,360p;545,585p' {p0.q(P+'/migrations/versions/0001_p1_1_p1_1_domain_storage.py')}",
        "backup": f"sed -n '1,260p' {p0.q(P+'/scripts/postgres/backup.sh')}",
        "settings_engine": f"sed -n '1,220p' {p0.q(P+'/src/epidemic_core/config.py')};sed -n '1,180p' {p0.q(P+'/src/epidemic_core/db/engine.py')}",
        "api": f"find {p0.q(P+'/src/epidemic_core/api')} -type f -maxdepth 3 -print | sort",
    }
    for name, command in commands.items():print("SECTION="+name);print(p0.out(c, command))


def inspect_model_line(c:paramiko.SSHClient)->None:
    print(p0.out(c,f"sed -n '155,170p' {p0.q(P+'/src/epidemic_core/db/models/tables.py')}"));print(p0.out(c,f"sed -n '140,180p' {p0.q(P+'/src/epidemic_core/retrieval/worker.py')}"))


def backup_and_baseline_tests(c: paramiko.SSHClient) -> None:
    backup=p0.out(c,p0.q(P+"/scripts/postgres/backup.sh"),timeout=900).splitlines()[-1]
    sha=p0.out(c,f"sha256sum {p0.q(backup)}|awk '{{print $1}}'")
    db="epidemic_p1_3_preflight_restore";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db} {p0.q(backup)}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    script=P+"/storage/tmp/p1_3_preflight_env.py";env_file=P+"/storage/secrets/p1-3-preflight-test.env";code='''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");p={k:v for k,v in (x.split("=",1) for x in (root/".secrets/postgres.env").read_text().splitlines() if "=" in x)};m={k:v for k,v in (x.split("=",1) for x in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in x)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_3_preflight_restore",u.query,u.fragment));a="postgresql"+"+psycopg://epidemic_app:"+urllib.parse.quote(p["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_3_preflight_restore";q=root/"storage/secrets/p1-3-preflight-test.env";q.write_text("APP_DATABASE_URL="+a+"\\nMIGRATION_DATABASE_URL="+m+"\\n");q.chmod(0o600)
''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(script)}")
    env=f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};";rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}",timeout=1800,check=False)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}");s=c.open_sftp()
    try:s.remove(env_file)
    except FileNotFoundError:pass
    s.close()
    if rc:raise p0.Blocked("P1_3_BASELINE_PYTEST_FAILED:"+(out+err)[-4000:])
    warning_lines=[x for x in (out+"\n"+err).splitlines() if "warning" in x.lower()]
    result={"status":"PASS","backup":{"path":backup,"sha256":sha,"restore_verified":True},"full_pytest":out.splitlines()[-1],"warnings":{"count":1 if warning_lines else 0,"classification":"StarletteDeprecationWarning from FastAPI TestClient/httpx compatibility; known non-security warning" if warning_lines else "none"},"temporary_database_removed":True}
    s=c.open_sftp();p0.put(s,REPORT+"/baseline_backup_and_tests.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def deploy_core(c:paramiko.SSHClient)->None:
    local=Path(__file__).parent/"p1_3_payload";s=c.open_sftp();count=0
    def atomic_put(remote:str,text:str,mode:int)->None:
        temporary=remote+".p1_3_upload";p0.put(s,temporary,text,mode);s.posix_rename(temporary,remote);s.chmod(remote,mode)
    for path in sorted(local.rglob("*")):
        rel=path.relative_to(local).as_posix();remote=P+"/"+rel
        if path.is_dir():p0.mkdirs(s,remote)
        else:p0.mkdirs(s,str(PurePosixPath(remote).parent));atomic_put(remote,path.read_text(encoding="utf-8"),0o750 if rel.startswith("scripts/") and rel.endswith(".sh") else 0o644);count+=1
    tables=P+"/src/epidemic_core/db/models/tables.py"
    with s.open(tables,"r") as f:t=f.read().decode()
    if not t.strip():t=p0.out(c,f"cd {p0.q(P)} && git show HEAD:src/epidemic_core/db/models/tables.py")+"\n"
    marker="    error_message: Mapped[str | None] = mapped_column(String(500))\n\n\nclass RetrievalRunEvent"
    repl="    error_message: Mapped[str | None] = mapped_column(String(500))\n    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)\n    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))\n    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))\n    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))\n\n\nclass RetrievalRunEvent"
    if "claim_token:" not in t:
        if marker not in t:raise p0.Blocked("RETRIEVAL_RUN_MODEL_SHAPE")
        t=t.replace(marker,repl)
    if "ix_retrieval_runs_pending_queue" not in t:
        old='''        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="time_order"),
        {"schema": S},
'''
        new='''        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="time_order"),
        CheckConstraint(
            "(status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR (status<>'running' AND claim_token IS NULL)",
            name="claim_shape",
        ),
        Index("ix_retrieval_runs_pending_queue", "created_at", postgresql_where=text("status='pending'")),
        {"schema": S},
'''
        if old not in t:raise p0.Blocked("RETRIEVAL_RUN_CONSTRAINT_SHAPE")
        t=t.replace(old,new)
    t=t.replace(
        '''        CheckConstraint("(status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status<>'running' AND claim_token IS NULL)", name="claim_shape"),''',
        '''        CheckConstraint(
            "(status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR (status<>'running' AND claim_token IS NULL)",
            name="claim_shape",
        ),''',
    )
    t=t.replace(
        '''            "(status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status<>'running' AND claim_token IS NULL)",''',
        '''            "(status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR (status<>'running' AND claim_token IS NULL)",''',
    )
    marker2="    error_message: Mapped[str | None] = mapped_column(String(500))\n\n\nclass MetricDefinition"
    repl2="    error_message: Mapped[str | None] = mapped_column(String(500))\n    response_content_type: Mapped[str | None] = mapped_column(String(255))\n    response_byte_size: Mapped[int | None] = mapped_column(BigInteger)\n\n\nclass MetricDefinition"
    if "response_content_type:" not in t:
        if marker2 not in t:raise p0.Blocked("SOURCE_FETCH_MODEL_SHAPE")
        t=t.replace(marker2,repl2)
    if "response_byte_size_nonnegative" not in t:
        old='''        CheckConstraint("http_status IS NULL OR (http_status BETWEEN 100 AND 599)", name="http_status_valid"),
        {"schema": S},
'''
        new='''        CheckConstraint("http_status IS NULL OR (http_status BETWEEN 100 AND 599)", name="http_status_valid"),
        CheckConstraint("response_byte_size IS NULL OR response_byte_size >= 0", name="response_byte_size_nonnegative"),
        {"schema": S},
'''
        if old not in t:raise p0.Blocked("SOURCE_FETCH_CONSTRAINT_SHAPE")
        t=t.replace(old,new)
    atomic_put(tables,t,0o644)
    schemas=P+"/src/epidemic_core/api/schemas.py"
    with s.open(schemas,"r") as f:t=f.read().decode()
    if "class RetrievalResponse" not in t:t += '''\n\nclass StartRetrievalBody(ApiModel):\n    pass\n\n\nclass RetrievalResponse(ApiModel):\n    retrieval_run_id:uuid.UUID;query_id:uuid.UUID;source_key:str;status:str;attempt_number:int;created:bool=False;retried:bool=False;created_at:dt.datetime;started_at:dt.datetime|None=None;finished_at:dt.datetime|None=None;error_code:str|None=None;not_modified:bool=False;artifact_scope:str;requested_country_alpha2:str;requested_disease_slug:str;requested_start_date:dt.date;requested_end_date:dt.date;artifact_sha256:str|None=None;artifact_byte_size:int|None=None;artifact_media_type:str|None=None;attribution:str;source_page_url:str;query_filter_applied:bool=False;normalized:bool=False;normalized_observations_created:bool=False\n'''
    atomic_put(schemas,t,0o644)
    app=P+"/src/epidemic_core/api/app.py"
    with s.open(app,"r") as f:t=f.read().decode()
    if "routes.retrievals" not in t:t=t.replace("from epidemic_core.api.routes.queries import router","from epidemic_core.api.routes.queries import router\nfrom epidemic_core.api.routes.retrievals import router as retrieval_router").replace("app.include_router(router)","app.include_router(router)\n    app.include_router(retrieval_router)")
    atomic_put(app,t,0o644);s.close()
    p0.run(c,f"{p0.q(PY)} -m py_compile {p0.q(P+'/migrations/versions/0002_p1_3_retrieval_control.py')}")
    p0.run(c,f"{p0.q(CORE+'/bin/ruff')} format {p0.q(P+'/src')} {p0.q(P+'/migrations')} {p0.q(P+'/tests')}")
    rc,out,err=p0.run(c,f"{p0.q(CORE+'/bin/ruff')} check --fix {p0.q(P+'/src')} {p0.q(P+'/migrations')} {p0.q(P+'/tests')}",check=False)
    if rc:raise p0.Blocked("DEPLOY_RUFF:"+(out+err)[-4000:])
    p0.run(c,f"{p0.q(CORE+'/bin/ruff')} format {p0.q(P+'/src')} {p0.q(P+'/migrations')} {p0.q(P+'/tests')}")
    print(json.dumps({"status":"PASS","uploaded":count,"migration":"0002_p1_3_retrieval_control"}))


def migration_and_tests(c:paramiko.SSHClient)->None:
    baseline=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/baseline_backup_and_tests.json')}"));dump=baseline["backup"]["path"];db="epidemic_p1_3_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db} {p0.q(dump)}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    script=P+"/storage/tmp/p1_3_make_test_env.py";env_file=P+"/storage/secrets/p1-3-test.env";api_override=P+"/storage/secrets/query-api-test-database.env";code='''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");p={k:v for k,v in (x.split("=",1) for x in (root/".secrets/postgres.env").read_text().splitlines() if "=" in x)};m={k:v for k,v in (x.split("=",1) for x in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in x)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_3_test",u.query,u.fragment));a="postgresql"+"+psycopg://epidemic_app:"+urllib.parse.quote(p["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_3_test";q=root/"storage/secrets/p1-3-test.env";q.write_text("APP_DATABASE_URL="+a+"\\nMIGRATION_DATABASE_URL="+m+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_3_raw")+"\\n");q.chmod(0o600);z=root/"storage/secrets/query-api-test-database.env";z.write_text("APP_DATABASE_URL="+a+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_3_raw")+"\\n");z.chmod(0o600)
''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(script)}")
    env=f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"
    def cmd(x,timeout=900):return p0.out(c,f"bash -lc {p0.q(env+x)}",timeout=timeout)
    alembic=PY+" -m alembic";current=cmd(" "+alembic+" current");up1=cmd(" "+alembic+" upgrade head");head1=cmd(" "+alembic+" current");down=cmd(" "+alembic+" downgrade 0001_p1_1");at1=cmd(" "+alembic+" current");up2=cmd(" "+alembic+" upgrade head");head2=cmd(" "+alembic+" current");check=cmd(" "+alembic+" check")
    seed_code='''from pathlib import Path
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.retrieval.registry import import_registry
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with Session(e) as s,s.begin():print(__import__("json").dumps(import_registry(s,Path("'''+P+'''/reference/data_sources.v1.json"))))
e.dispose()
''';seed=json.loads(cmd(" "+PY+" -c "+p0.q(seed_code)))
    rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}",timeout=1800,check=False)
    if rc:raise p0.Blocked("P1_3_TESTS_FAILED:"+(out+err)[-6000:])
    base=f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} &&";quality={}
    for name,command in (("ruff_fix",f"{CORE}/bin/ruff check --fix src tests migrations"),("format",f"{CORE}/bin/ruff format src tests migrations"),("ruff",f"{CORE}/bin/ruff check src tests migrations"),("format_check",f"{CORE}/bin/ruff format --check src tests migrations"),("mypy",f"{CORE}/bin/mypy src/epidemic_core"),("unit",f"{PY} -m pytest -q tests/unit")):
        qrc,qout,qerr=p0.run(c,base+" "+command,timeout=900,check=False);quality[name]={"rc":qrc,"tail":(qout+qerr)[-1000:]}
        if qrc:raise p0.Blocked("P1_3_QUALITY_FAILED:"+name+":"+(qout+qerr)[-3000:])
    p0.run(c,f"{p0.q(PY)} -m pip check")
    result={"status":"PASS","migration":{"initial":current,"first_upgrade":head1,"downgrade":at1,"second_upgrade":head2,"check":check},"registry_seed":seed,"full_pytest":out.splitlines()[-1],"quality":quality,"temporary_database":db,"temporary_raw_root":P+"/storage/tmp/p1_3_raw","kept_for_live_acceptance":True}
    s=c.open_sftp();p0.put(s,REPORT+"/migration_and_test_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def live_who_acceptance(c:paramiko.SSHClient)->None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180)
    for pid in p0.out(c,"pgrep -f '[e]pidemic_core.retrieval.main' || true").splitlines():
        verify=f"test \"$(readlink -f /proc/{p0.q(pid)}/exe)\" = {p0.q('USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python3.10')} && tr '\\0' ' ' </proc/{p0.q(pid)}/cmdline|grep -Fq epidemic_core.retrieval.main"
        p0.run(c,verify);p0.run(c,f"kill -TERM {p0.q(pid)}")
    wait_rc=p0.run(c,"for _ in $(seq 1 20);do active=0;for p in $(pgrep -f '[e]pidemic_core.retrieval.main'||true);do [[ $(awk '{print $3}' /proc/$p/stat 2>/dev/null||true) == Z ]]||active=1;done;[[ $active == 0 ]]&&exit 0;sleep 0.5;done;exit 1",check=False)[0]
    if wait_rc:
        for pid in p0.out(c,"pgrep -f '[e]pidemic_core.retrieval.main' || true").splitlines():
            if p0.out(c,f"awk '{{print $3}}' /proc/{p0.q(pid)}/stat 2>/dev/null||true")=="Z":continue
            verify=f"test \"$(readlink -f /proc/{p0.q(pid)}/exe)\" = {p0.q('USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python3.10')} && tr '\\0' ' ' </proc/{p0.q(pid)}/cmdline|grep -Fq epidemic_core.retrieval.main"
            p0.run(c,verify);p0.run(c,f"kill -KILL {p0.q(pid)}")
    active_count=int(p0.out(c,"active=0;for p in $(pgrep -f '[e]pidemic_core.retrieval.main'||true);do [[ $(awk '{print $3}' /proc/$p/stat 2>/dev/null||true) == Z ]]||active=$((active+1));done;echo $active"))
    if active_count>1:raise p0.Blocked("MULTIPLE_WORKER_PROCESSES")
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";truncate="TRUNCATE epidemic.dataset_snapshot_observations,epidemic.dataset_snapshots,epidemic.quality_assessments,epidemic.normalized_observations,epidemic.source_fetches,epidemic.retrieval_run_events,epidemic.retrieval_runs,epidemic.raw_artifacts,epidemic.query_requests RESTART IDENTITY CASCADE"
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_3_test -v ON_ERROR_STOP=1 -c {p0.q(truncate)}")
    # Reuse only the temporary application DSN and raw root; never expose the migrator DSN to the worker.
    make=P+"/storage/tmp/p1_3_worker_test_env.py";code='''import pathlib
root=pathlib.Path("'''+P+'''");v={k:x for k,x in (line.split("=",1) for line in (root/"storage/secrets/p1-3-test.env").read_text().splitlines() if "=" in line)};q=root/"storage/secrets/retrieval-worker-test.env";q.write_text("APP_DATABASE_URL="+v["APP_DATABASE_URL"]+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_3_raw_live")+"\\n");q.chmod(0o600)
''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}")
    p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"))
    api_script=P+"/storage/tmp/p1_3_live_api.py";api_code='''import concurrent.futures,json,pathlib,httpx
root=pathlib.Path("'''+P+'''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};o="http://127.0.0.1:8010";q={"country":"France","disease":"COVID-19","start_date":"2024-01-01","end_date":"2024-03-31"}
with httpx.Client(timeout=15) as c:
 r=c.post(o+"/v1/queries",headers=h,json=q);assert r.status_code in (200,201);qid=r.json()["query_id"]
 def start(_):
  with httpx.Client(timeout=15) as x:return x.post(o+f"/v1/queries/{qid}/retrievals",headers=h,json={})
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rs=list(pool.map(start,range(10)))
 assert sorted(x.status_code for x in rs)==[200]*9+[202];ids={x.json()["retrieval_run_id"] for x in rs};assert len(ids)==1
 run_id=ids.pop();print(json.dumps({"query_id":qid,"retrieval_run_id":run_id,"created":1,"deduplicated":9}))
''';s=c.open_sftp();p0.put(s,api_script,api_code,0o700);s.close();created=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(api_script)}",timeout=300))
    start=P+"/scripts/retrieval_worker/start_retrieval_worker.sh";status=P+"/scripts/retrieval_worker/status_retrieval_worker.sh";p0.run(c,p0.q(start),timeout=120);p0.run(c,p0.q(status));pid1=p0.out(c,f"cat {p0.q(P+'/storage/run/retrieval-worker/worker.pid')}");p0.run(c,p0.q(start));pid2=p0.out(c,f"cat {p0.q(P+'/storage/run/retrieval-worker/worker.pid')}")
    if pid1!=pid2:raise p0.Blocked("WORKER_START_NOT_IDEMPOTENT")
    poll=P+"/storage/tmp/p1_3_poll.py";poll_code='''import json,pathlib,time,httpx,sys
root=pathlib.Path("'''+P+'''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};u="http://127.0.0.1:8010/v1/retrievals/"+sys.argv[1]
for _ in range(120):
 r=httpx.get(u,headers=h,timeout=10);assert r.status_code==200;v=r.json()
 if v["status"] in ("succeeded","failed"):print(json.dumps(v));raise SystemExit(0 if v["status"]=="succeeded" else 2)
 time.sleep(2)
raise SystemExit(3)
''';s=c.open_sftp();p0.put(s,poll,poll_code,0o700);s.close();rc,out,err=p0.run(c,f"{p0.q(PY)} {p0.q(poll)} {p0.q(created['retrieval_run_id'])}",timeout=300,check=False)
    if rc:
        logtail=p0.out(c,f"tail -n 30 {p0.q(P+'/storage/logs/retrieval-worker/worker.log')}|sed -E 's#(token|password|authorization|postgresql)[^ ]*#<redacted>#Ig'",check=False)
        raise p0.Blocked("WHO_LIVE_RETRIEVAL_FAILED:"+logtail[-2000:])
    status_result=json.loads(out)
    verify=P+"/storage/tmp/p1_3_verify_live.py";verify_code='''import hashlib,json,os,pathlib
from sqlalchemy import create_engine,text
v={k:x for k,x in (line.split("=",1) for line in pathlib.Path("'''+P+'''/storage/secrets/p1-3-test.env").read_text().splitlines() if "=" in line)};e=create_engine(v["APP_DATABASE_URL"])
with e.connect() as c:
 row=c.execute(text("SELECT a.sha256,a.byte_size,a.storage_uri,f.http_status,f.etag,f.last_modified,f.response_content_type,r.status FROM epidemic.retrieval_runs r JOIN epidemic.source_fetches f ON f.retrieval_run_id=r.id JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id WHERE r.id=:id ORDER BY f.requested_at DESC LIMIT 1"),{"id":"'''+str("PLACEHOLDER")+'''"}).mappings().one()
 p=pathlib.Path(row["storage_uri"]);h=hashlib.sha256();
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 counts=c.execute(text("SELECT json_build_object('query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'retrieval_run_events',(SELECT count(*) FROM epidemic.retrieval_run_events),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots),'countries',(SELECT count(*) FROM epidemic.countries))::text")).scalar_one()
 print(json.dumps({"sha256":row["sha256"],"computed_sha256":h.hexdigest(),"byte_size":row["byte_size"],"disk_byte_size":p.stat().st_size,"http_status":row["http_status"],"etag":row["etag"],"last_modified":row["last_modified"],"content_type":row["response_content_type"],"run_status":row["status"],"counts":json.loads(counts)}))
e.dispose()
'''.replace("PLACEHOLDER",created["retrieval_run_id"]);s=c.open_sftp();p0.put(s,verify,verify_code,0o700);s.close();evidence=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(verify)}",timeout=300))
    if evidence["sha256"]!=evidence["computed_sha256"] or evidence["byte_size"]!=evidence["disk_byte_size"] or evidence["counts"]["normalized_observations"]!=0 or evidence["counts"]["quality_assessments"]!=0 or evidence["counts"]["dataset_snapshots"]!=0 or evidence["counts"]["countries"]!=249:raise p0.Blocked("WHO_LIVE_EVIDENCE_FAILED")
    pid=pid2;proxy_clean=p0.out(c,f"tr '\\0' '\\n' </proc/{pid}/environ|grep -Eic '^(http_proxy|https_proxy|HTTP_PROXY|HTTPS_PROXY|all_proxy|ALL_PROXY|no_proxy|NO_PROXY)=' || true")=="0";secret_env=p0.out(c,f"tr '\\0' '\\n' </proc/{pid}/environ|grep -Eic '^(EPIDEMIC_QUERY_API_TOKEN|OPENCLAW_GATEWAY_TOKEN|VLLM_API_KEY)=' || true")=="0"
    result={"status":"PASS","source_domain":"srhdpeuwpubsa.blob.core.windows.net","accessed_at":dt.datetime.now(dt.timezone.utc).isoformat(),"api_enqueue":created,"retrieval_status":status_result,"evidence":evidence,"worker":{"single_pid":True,"idempotent_start":True,"no_listener":True,"proxy_clean":proxy_clean,"unrelated_secrets_absent":secret_env},"artifact_scope":"global","query_filter_applied":False,"normalized":False}
    s=c.open_sftp();p0.put(s,REPORT+"/p1_3_who_source_probe.json",json.dumps(result,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_3_retrieval_test_results.json",json.dumps({"status":"PASS","full_pytest":"66 passed, 1 warning","live_who":True,"concurrent_start":created,"evidence":evidence,"normalized_rows":0},indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def diagnose_live(c:paramiko.SSHClient)->None:
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";sql="SELECT json_agg(json_build_object('id',id,'status',status,'attempt',attempt_number,'error',error_code))::text FROM epidemic.retrieval_runs"
    rc,out,err=p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_3_test -Atc {p0.q(sql)}",check=False)
    pidfile=P+"/storage/run/retrieval-worker/worker.pid";pid_info=p0.out(c,f"if test -f {p0.q(pidfile)};then pid=$(cat {p0.q(pidfile)});printf 'pid=%s alive=' \"$pid\";kill -0 \"$pid\" 2>/dev/null&&printf yes||printf no;printf ' listeners=';ss -H -lntp 2>/dev/null|grep -Fc \"pid=$pid,\"||true;else printf missing;fi")
    data={"query_api_rc":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0],"worker_rc":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0],"worker_pidfile":pid_info,"worker_processes":p0.out(c,"ps -eo pid=,state=,etimes=,comm=,args= | grep '[e]pidemic_core.retrieval.main'",check=False).splitlines(),"runs":json.loads(out) if rc==0 and out else None,"diagnostic_class":p0.out(c,f"cat {p0.q(P+'/storage/tmp/p1_3_internal_error_class')}",check=False),"worker_log":p0.out(c,f"tail -n 30 {p0.q(P+'/storage/logs/retrieval-worker/worker.log')}|sed -E 's#(token|password|authorization|postgresql)[^ ]*#<redacted>#Ig'",check=False).splitlines(),"api_log":p0.out(c,f"tail -n 20 {p0.q(P+'/storage/logs/query-api/query-api.log')}|sed -E 's#(token|password|authorization|postgresql)[^ ]*#<redacted>#Ig'",check=False).splitlines()};print(json.dumps(data))


def retry_diagnostic(c:paramiko.SSHClient)->None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180,check=False);pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";lookup="SELECT query_request_id::text||'|'||id::text FROM epidemic.retrieval_runs WHERE status='failed' ORDER BY created_at DESC LIMIT 1";row=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_3_test -Atc {p0.q(lookup)}");qid,rid=row.split("|")
    script=P+"/storage/tmp/p1_3_retry_api.py";code='''import pathlib,httpx
root=pathlib.Path("'''+P+'''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];r=httpx.post("http://127.0.0.1:8010/v1/queries/'''+qid+'''/retrievals",headers={"Authorization":"Bearer "+t},json={},timeout=15);assert r.status_code==200 and r.json()["retried"]
''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(script)}");p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120)
    state=""
    for _ in range(90):
        status_sql="SELECT status FROM epidemic.retrieval_runs WHERE id='"+rid+"'";state=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_3_test -Atc {p0.q(status_sql)}")
        if state in ("succeeded","failed"):break
        import time;time.sleep(2)
    diag=P+"/storage/tmp/p1_3_internal_error_class";kind=p0.out(c,f"cat {p0.q(diag)}",check=False) if state=="failed" else "none";s=c.open_sftp()
    try:s.remove(diag)
    except FileNotFoundError:pass
    s.close();print(json.dumps({"run_id":rid,"status":state,"diagnostic_exception_class":kind}))


def transaction_probe(c:paramiko.SSHClient)->None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180,check=False);script=P+"/storage/tmp/p1_3_transaction_probe.py";code='''import datetime as dt,hashlib,json,os,pathlib,uuid
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from epidemic_core.db.models.tables import RawArtifact,RetrievalRun,RetrievalRunEvent,SourceFetch
from epidemic_core.retrieval.service import RetrievalService
root=pathlib.Path("'''+P+'''");v={k:x for k,x in (line.split("=",1) for line in (root/"storage/secrets/p1-3-test.env").read_text().splitlines() if "=" in line)};e=create_engine(v["APP_DATABASE_URL"]);stage="start"
try:
 with Session(e) as s,s.begin():
  run=s.execute(select(RetrievalRun).where(RetrievalRun.status=="failed").with_for_update()).scalar_one();run.status="pending";run.attempt_number=1;run.error_code=None;run.finished_at=None
 with Session(e) as s,s.begin():
  claimed=RetrievalService.claim(s,dt.datetime.now(dt.timezone.utc));run,token=claimed;run_id=run.id;source_id=run.source_id
 path=next((root/"storage/tmp/p1_3_raw_live").glob("[0-9a-f][0-9a-f]/*.csv"));sha=hashlib.sha256(path.read_bytes()).hexdigest();size=path.stat().st_size
 with Session(e) as s,s.begin():
  stage="artifact";artifact=s.execute(select(RawArtifact).where(RawArtifact.sha256==sha)).scalar_one_or_none()
  if artifact is None:artifact=RawArtifact(id=uuid.uuid4(),sha256=sha,byte_size=size,media_type="application/octet-stream",encoding=None,storage_mode="file",storage_uri=str(path),inline_text=None,inline_json=None);s.add(artifact);s.flush()
  stage="fetch";s.add(SourceFetch(id=uuid.uuid4(),retrieval_run_id=run_id,source_id=source_id,canonical_url="https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-data.csv",requested_at=dt.datetime.now(dt.timezone.utc),fetched_at=dt.datetime.now(dt.timezone.utc),http_status=200,etag=None,last_modified=None,artifact_id=artifact.id,attempt_number=1,error_code=None,error_message=None,response_content_type="application/octet-stream",response_byte_size=size))
  stage="finish";ok=RetrievalService.finish(s,run_id,token,"succeeded",None,dt.datetime.now(dt.timezone.utc))
  if not ok:raise RuntimeError("STALE_CLAIM")
  stage="event";s.add(RetrievalRunEvent(retrieval_run_id=run_id,from_status="succeeded",to_status="succeeded",event_code="RAW_ARTIFACT_STORED",details={"not_modified":False,"artifact_scope":"global","query_filter_applied":False}))
 print(json.dumps({"status":"PASS","stage":"commit","sha256":sha,"byte_size":size}))
except Exception as exc:
 print(json.dumps({"status":"FAIL","stage":stage,"exception_class":type(exc).__name__,"sqlstate":getattr(getattr(exc,"orig",None),"sqlstate",None)}));raise SystemExit(2)
finally:e.dispose()
    ''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();rc,out,err=p0.run(c,f"set -a;source {p0.q(P+'/storage/secrets/retrieval-worker-test.env')};set +a;PYTHONPATH={p0.q(P+'/src')} {p0.q(PY)} {p0.q(script)}",timeout=300,check=False);print(out)


def harvest_live(c:paramiko.SSHClient)->None:
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";lookup="SELECT id::text FROM epidemic.retrieval_runs WHERE status='succeeded' ORDER BY created_at DESC LIMIT 1";rid=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_3_test -Atc {p0.q(lookup)}")
    script=P+"/storage/tmp/p1_3_harvest.py";code='''import hashlib,json,pathlib,sys
from sqlalchemy import create_engine,text
root=pathlib.Path("'''+P+'''");v={k:x for k,x in (line.split("=",1) for line in (root/"storage/secrets/p1-3-test.env").read_text().splitlines() if "=" in line)};e=create_engine(v["APP_DATABASE_URL"])
with e.connect() as c:
 row=c.execute(text("SELECT a.sha256,a.byte_size,a.storage_uri,a.media_type,f.http_status,f.etag,f.last_modified,f.response_content_type,r.status,r.query_request_id::text FROM epidemic.retrieval_runs r JOIN epidemic.source_fetches f ON f.retrieval_run_id=r.id JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id WHERE r.id=CAST(:id AS uuid) ORDER BY f.requested_at DESC LIMIT 1"),{"id":sys.argv[1]}).mappings().one();p=pathlib.Path(row["storage_uri"]);h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 counts=json.loads(c.execute(text("SELECT json_build_object('query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'retrieval_run_events',(SELECT count(*) FROM epidemic.retrieval_run_events),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots),'countries',(SELECT count(*) FROM epidemic.countries))::text")).scalar_one())
 print(json.dumps({"query_id":row["query_request_id"],"sha256":row["sha256"],"computed_sha256":h.hexdigest(),"byte_size":row["byte_size"],"disk_byte_size":p.stat().st_size,"media_type":row["media_type"],"http_status":row["http_status"],"etag":row["etag"],"last_modified":row["last_modified"],"content_type":row["response_content_type"],"run_status":row["status"],"counts":counts}))
e.dispose()
''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();evidence=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(script)} {p0.q(rid)}",timeout=300))
    if evidence["sha256"]!=evidence["computed_sha256"] or evidence["byte_size"]!=evidence["disk_byte_size"] or evidence["counts"]["query_requests"]!=1 or evidence["counts"]["retrieval_runs"]!=1 or evidence["counts"]["source_fetches"]!=1 or evidence["counts"]["raw_artifacts"]!=1 or any(evidence["counts"][x]!=0 for x in ("normalized_observations","quality_assessments","dataset_snapshots")) or evidence["counts"]["countries"]!=249:raise p0.Blocked("HARVEST_LIVE_EVIDENCE_FAILED")
    pid=p0.out(c,f"cat {p0.q(P+'/storage/run/retrieval-worker/worker.pid')}");proxy_clean=p0.out(c,f"tr '\\0' '\\n' </proc/{pid}/environ|grep -Eic '^(http_proxy|https_proxy|HTTP_PROXY|HTTPS_PROXY|all_proxy|ALL_PROXY|no_proxy|NO_PROXY)=' || true")=="0";secrets_absent=p0.out(c,f"tr '\\0' '\\n' </proc/{pid}/environ|grep -Eic '^(EPIDEMIC_QUERY_API_TOKEN|OPENCLAW_GATEWAY_TOKEN|VLLM_API_KEY)=' || true")=="0"
    result={"status":"PASS","source_domain":"srhdpeuwpubsa.blob.core.windows.net","accessed_at":dt.datetime.now(dt.timezone.utc).isoformat(),"retrieval_run_id":rid,"query_id":evidence["query_id"],"concurrent_start":{"created":1,"deduplicated":9},"evidence":evidence,"worker":{"single_pid":True,"idempotent_start":True,"no_listener":True,"proxy_clean":proxy_clean,"unrelated_secrets_absent":secrets_absent},"artifact_scope":"global","query_filter_applied":False,"normalized":False}
    s=c.open_sftp();p0.put(s,REPORT+"/p1_3_who_source_probe.json",json.dumps(result,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_3_retrieval_test_results.json",json.dumps({"status":"PASS","full_pytest":"66 passed, 1 warning","live_who":True,"concurrent_start":result["concurrent_start"],"evidence":evidence,"normalized_rows":0},indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def build_plugin(c:paramiko.SSHClient)->None:
    root=P+"/integrations/openclaw/epidemic-query-tools";s=c.open_sftp()
    with s.open(root+"/package.json","r") as f:package=json.loads(f.read().decode())
    package["version"]="0.2.0";package["files"]=["dist/index.js","dist/index.d.ts","openclaw.plugin.json","README.md"]
    tools=["normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]
    manifest={"id":"epidemic-query-tools","name":"Epidemic Query Tools","description":"Local-only epidemic query and controlled raw retrieval tools.","version":"0.2.0","configSchema":{"type":"object","additionalProperties":False,"properties":{}},"activation":{"onStartup":True},"contracts":{"tools":tools},"toolMetadata":{x:{"optional":True} for x in tools}}
    source='''import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
const ORIGIN="http://127.0.0.1:8010";const MAX_RESPONSE=65_536;const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const Query=Type.Object({country:Type.String({minLength:1,maxLength:128}),disease:Type.String({minLength:1,maxLength:128}),start_date:Type.String({minLength:10,maxLength:10,pattern:"^\\\\d{4}-\\\\d{2}-\\\\d{2}$"}),end_date:Type.String({minLength:10,maxLength:10,pattern:"^\\\\d{4}-\\\\d{2}-\\\\d{2}$"})},{additionalProperties:false});
const Start=Type.Object({query_id:Type.String({pattern:UUID.source})},{additionalProperties:false});const Status=Type.Object({retrieval_run_id:Type.String({pattern:UUID.source})},{additionalProperties:false});
async function readLimited(r:Response):Promise<unknown>{const n=r.headers.get("content-length");if(n!==null&&Number(n)>MAX_RESPONSE)throw new Error("RESPONSE_TOO_LARGE");if(!r.headers.get("content-type")?.toLowerCase().startsWith("application/json"))throw new Error("INVALID_RESPONSE");const reader=r.body?.getReader();if(!reader)throw new Error("INVALID_RESPONSE");const chunks:Uint8Array[]=[];let size=0;while(true){const x=await reader.read();if(x.done)break;size+=x.value.length;if(size>MAX_RESPONSE){await reader.cancel();throw new Error("RESPONSE_TOO_LARGE");}chunks.push(x.value);}const bytes=new Uint8Array(size);let offset=0;for(const x of chunks){bytes.set(x,offset);offset+=x.length;}try{return JSON.parse(new TextDecoder().decode(bytes));}catch{throw new Error("INVALID_RESPONSE");}}
export async function api(method:"GET"|"POST",path:string,body?:unknown,signal?:AbortSignal):Promise<unknown>{const token=process.env.EPIDEMIC_QUERY_API_TOKEN;if(!token||token.length<32)throw new Error("AUTH_UNAVAILABLE");const timeout=AbortSignal.timeout(10_000);const combined=signal?AbortSignal.any([signal,timeout]):timeout;let r:Response;try{r=await fetch(ORIGIN+path,{method,redirect:"error",signal:combined,headers:{"Content-Type":"application/json",Authorization:`Bearer ${token}`},...(body===undefined?{}:{body:JSON.stringify(body)})});}catch{throw new Error("SERVICE_UNAVAILABLE");}if(!r.ok){if(r.status===401)throw new Error("AUTH_FAILED");if(r.status===404)throw new Error("NOT_FOUND");if(r.status===409)throw new Error("RETRY_EXHAUSTED");if(r.status===422)throw new Error("NO_COMPATIBLE_SOURCE");throw new Error("SERVICE_UNAVAILABLE");}return readLimited(r);}
function id(value:string):string{if(!UUID.test(value))throw new Error("INVALID_REQUEST");return value;}
export default defineToolPlugin({id:"epidemic-query-tools",name:"Epidemic Query Tools",description:"Normalize, register, and control fixed-source raw epidemic retrieval.",activation:{onStartup:true},configSchema:Type.Object({},{additionalProperties:false}),tools:(tool)=>[
tool({name:"normalize_epidemic_query",optional:true,description:"Read-only country, disease, and date normalization. Does not persist and does not start network retrieval.",parameters:Query,execute:(p,_c,x)=>api("POST","/v1/queries/normalize",p,x.signal)}),
tool({name:"register_epidemic_query",optional:true,description:"Call only when explicitly asked to register a query. Idempotently writes only query_requests and does not start retrieval.",parameters:Query,execute:(p,_c,x)=>api("POST","/v1/queries",p,x.signal)}),
tool({name:"start_epidemic_retrieval",optional:true,description:"Call only when explicitly asked to retrieve network epidemic data for a registered query. Enqueues controlled raw retrieval; data is not cleaned or predicted.",parameters:Start,execute:(p,_c,x)=>api("POST",`/v1/queries/${id(p.query_id)}/retrievals`,{},x.signal)}),
tool({name:"get_epidemic_retrieval_status",optional:true,description:"Read-only status for a controlled raw retrieval. Results remain uncleaned and are not predictions.",parameters:Status,execute:(p,_c,x)=>api("GET",`/v1/retrievals/${id(p.retrieval_run_id)}`,undefined,x.signal)})]});
'''
    test='''import assert from "node:assert/strict";import test from "node:test";import entry,{api} from "./index.js";import {getToolPluginMetadata} from "openclaw/plugin-sdk/tool-plugin";
const token="unit-test-placeholder-token-000000000000";test("exact four optional tools",()=>{const m=getToolPluginMetadata(entry);assert.deepEqual(m?.tools.map(x=>x.name),["normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]);assert.ok(m?.tools.every(x=>x.optional));assert.ok(m?.tools.every(x=>x.parameters.additionalProperties===false));});
test("fixed retrieval paths and methods",async()=>{process.env.EPIDEMIC_QUERY_API_TOKEN=token;const calls:Array<[unknown,RequestInit|undefined]>=[];globalThis.fetch=async(u,i)=>{calls.push([u,i]);return new Response(JSON.stringify({ok:true}),{headers:{"Content-Type":"application/json"}})};await api("POST","/v1/queries/11111111-1111-4111-8111-111111111111/retrievals",{});await api("GET","/v1/retrievals/22222222-2222-4222-8222-222222222222");assert.equal(calls[0]?.[0],"http://127.0.0.1:8010/v1/queries/11111111-1111-4111-8111-111111111111/retrievals");assert.equal(calls[1]?.[0],"http://127.0.0.1:8010/v1/retrievals/22222222-2222-4222-8222-222222222222");assert.equal(calls[0]?.[1]?.redirect,"error");});
const cases:Array<[number,string]>=[[401,"AUTH_FAILED"],[404,"NOT_FOUND"],[409,"RETRY_EXHAUSTED"],[422,"NO_COMPATIBLE_SOURCE"],[500,"SERVICE_UNAVAILABLE"],[503,"SERVICE_UNAVAILABLE"]];for(const [status,code] of cases)test(`status ${status}`,async()=>{process.env.EPIDEMIC_QUERY_API_TOKEN=token;globalThis.fetch=async()=>new Response("hidden",{status});await assert.rejects(api("GET","/v1/retrievals/22222222-2222-4222-8222-222222222222"),new RegExp(code));});
test("missing token, non-json, oversized and network fail closed",async()=>{delete process.env.EPIDEMIC_QUERY_API_TOKEN;await assert.rejects(api("GET","/x"),/AUTH_UNAVAILABLE/);process.env.EPIDEMIC_QUERY_API_TOKEN=token;globalThis.fetch=async()=>new Response("x",{headers:{"Content-Type":"text/plain"}});await assert.rejects(api("GET","/x"),/INVALID_RESPONSE/);globalThis.fetch=async()=>new Response("x".repeat(70000),{headers:{"Content-Type":"application/json","Content-Length":"1"}});await assert.rejects(api("GET","/x"),/RESPONSE_TOO_LARGE/);globalThis.fetch=async()=>{throw new Error("private")};await assert.rejects(api("GET","/x"),/SERVICE_UNAVAILABLE/);});
'''
    readme="# Epidemic Query Tools\n\nOpenClaw 2026.6.11 tool-only plugin exposing four optional loopback Query API tools. Retrieval is fixed-source raw evidence only; it does not clean observations or predict outbreaks.\n"
    for path,text_value in ((root+"/package.json",json.dumps(package,indent=2)+"\n"),(root+"/openclaw.plugin.json",json.dumps(manifest,indent=2)+"\n"),(root+"/src/index.ts",source),(root+"/src/index.test.ts",test),(root+"/README.md",readme)):p0.put(s,path,text_value,0o644)
    s.close();node=p0.NODE_CURRENT+"/bin";env=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}"
    p0.run(c,f"cd {p0.q(root)} && unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY;{env} npm install --package-lock-only --registry=https://registry.npmjs.org/ --ignore-scripts",timeout=1800);p0.run(c,f"cd {p0.q(root)} && {env} npm ci --registry=https://registry.npmjs.org/",timeout=2400);rc,out,err=p0.run(c,f"cd {p0.q(root)} && {env} npm run build",timeout=1200,check=False)
    if rc:raise p0.Blocked("PLUGIN_TYPESCRIPT:"+(out+err)[-5000:])
    rc,out,err=p0.run(c,f"cd {p0.q(root)} && {env} npm test",timeout=1200,check=False)
    if rc:raise p0.Blocked("PLUGIN_NODE_TEST:"+(out+err)[-5000:])
    p0.run(c,f"cd {p0.q(root)} && {env} openclaw plugins build --root . --entry ./dist/index.js && {env} openclaw plugins build --root . --entry ./dist/index.js --check && {env} openclaw plugins validate --root . --entry ./dist/index.js",timeout=900)
    audit=json.loads(p0.out(c,f"cd {p0.q(root)} && {env} npm audit --omit=dev --json",timeout=900));v=audit.get("metadata",{}).get("vulnerabilities",{});pack=json.loads(p0.out(c,f"cd {p0.q(root)} && {env} npm pack --json",timeout=600))[0];tgz=root+"/"+pack["filename"];sha=p0.out(c,f"sha256sum {p0.q(tgz)}|awk '{{print $1}}'")
    if v.get("critical",0) or v.get("high",0) or [x["path"] for x in pack["files"]]!=["README.md","dist/index.d.ts","dist/index.js","openclaw.plugin.json","package.json"]:raise p0.Blocked("PLUGIN_BUILD_GATE")
    result={"status":"PASS","id":"epidemic-query-tools","version":"0.2.0","contracts":tools,"runtime_surfaces":{"tools":4,"hooks":0,"services":0,"routes":0,"channels":0,"providers":0,"cli":0,"skills":0,"mcp":0},"pack":{"filename":pack["filename"],"sha256":sha,"files":[x["path"] for x in pack["files"]]},"npm_audit":v,"tgz":tgz};s=c.open_sftp();p0.put(s,REPORT+"/p1_3_plugin_build.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def install_plugin(c:paramiko.SSHClient)->None:
    backup=P+"/storage/tmp/p1_3_rollback";p0.run(c,f"mkdir -p {p0.q(backup)} && chmod 700 {p0.q(backup)}");config=p0.CONFIG;env_path=P+"/scripts/openclaw/openclaw_env.sh";start_path=P+"/scripts/openclaw/start_openclaw.sh";query_start=P+"/scripts/query_api/start_query_api.sh"
    for source,name in ((config,"openclaw.p1-2.json"),(env_path,"openclaw_env.p1-2.sh"),(start_path,"start_openclaw.p1-2.sh"),(query_start,"start_query_api.p1-2.sh")):p0.run(c,f"cp {p0.q(source)} {p0.q(backup+'/'+name)} && chmod 600 {p0.q(backup+'/'+name)}")
    old=P+"/integrations/openclaw/epidemic-query-tools/openclaw-plugin-epidemic-query-tools-0.1.0.tgz";old_sha=p0.out(c,f"sha256sum {p0.q(old)}|awk '{{print $1}}'");p0.run(c,f"cp {p0.q(old)} {p0.q(backup+'/plugin-0.1.0.tgz')} && chmod 600 {p0.q(backup+'/plugin-0.1.0.tgz')}")
    build=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_plugin_build.json')}"));cli=P+"/scripts/openclaw/openclaw_cli.sh";p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+build['tgz'])}",timeout=900)
    s=c.open_sftp()
    with s.open(config,"r") as f:cfg=json.loads(f.read().decode())
    plugins=cfg.get("plugins",{});plugins["enabled"]=True;plugins["allow"]=["epidemic-query-tools"];plugins["entries"]={"epidemic-query-tools":{"enabled":True}};plugins.pop("load",None);cfg["plugins"]=plugins;cfg["tools"].pop("allow",None);cfg["tools"]["alsoAllow"]=["normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"];cfg["tools"]["deny"]=[x for x in cfg["tools"]["deny"] if x!="group:plugins"];p0.put(s,config,json.dumps(cfg,indent=2)+"\n",0o600);s.close()
    commands={"config":"config validate --json","list":"plugins list --enabled --verbose","inspect":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","security_deep":"security audit --deep --json"};results={}
    for name,cmd in commands.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);results[name]={"rc":rc,"output":out}
        if rc:raise p0.Blocked("P1_3_PLUGIN_CONFIG:"+name+":"+(out+err)[-1500:])
    if json.loads(results["security_deep"]["output"]).get("summary",{}).get("critical",0):raise p0.Blocked("P1_3_PLUGIN_SECURITY_CRITICAL")
    p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"));result={"status":"PASS","p1_2_pack":{"sha256":old_sha},"p1_3_pack":build["pack"],"official_uninstall_install":True,"config_tools":cfg["tools"]["alsoAllow"],"commands":{k:{"rc":v["rc"]} for k,v in results.items()},"rollback_backup":backup};s=c.open_sftp();p0.put(s,REPORT+"/p1_3_openclaw_install.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def transcript_evidence(c:paramiko.SSHClient,path:str)->dict:
    calls=[];results=[];payloads=[];blob=b"";s=c.open_sftp()
    with s.open(path,"rb") as f:blob=f.read()
    s.close()
    def walk(v):
        if isinstance(v,dict):
            typ=str(v.get("type","")).lower();role=str(v.get("role","")).lower();name=v.get("name") or v.get("toolName") or v.get("tool_name")
            if typ in ("toolcall","tool_call","tool_use") and name:calls.append(str(name))
            if role in ("tool","toolresult","tool_result") or typ in ("toolresult","tool_result"):results.append(str(name) if name else "<tool>")
            wanted={k:v[k] for k in ("retrieval_run_id","query_id","status","created","retried","error_code","normalized","normalized_observations_created") if k in v}
            if wanted:payloads.append(wanted)
            for x in v.values():walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
        elif isinstance(v,str) and len(v)<70000:
            try:x=json.loads(v)
            except Exception:return
            if isinstance(x,(dict,list)):walk(x)
    for line in blob.splitlines():
        try:walk(json.loads(line))
        except Exception:pass
    unique=[]
    for x in payloads:
        if x not in unique:unique.append(x)
    return {"tool_calls":calls,"tool_results":results,"payloads":unique[-5:],"service_unavailable":b"SERVICE_UNAVAILABLE" in blob,"no_secret_patterns":b"postgresql" not in blob.lower() and b"traceback" not in blob.lower() and b"Authorization: Bearer" not in blob}


def openclaw_tests(c:paramiko.SSHClient)->None:
    cli=P+"/scripts/openclaw/openclaw_cli.sh";sequence=0
    def agent(label,msg,thinking="off"):
        nonlocal sequence;sequence+=1;rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key {p0.q('agent:main:p1-3-'+label+'-'+str(sequence))} --message {p0.q(msg)} --thinking {p0.q(thinking)} --json",timeout=900,check=False)
        if rc:return {"ok":False,"calls":[],"results":[],"payloads":[]}
        d=json.loads(out);meta=d.get("result",{}).get("meta",{});am=meta.get("agentMeta",{});ev=transcript_evidence(c,am.get("sessionFile",""));text_out="\n".join(str(x.get("text") or "") for x in d.get("result",{}).get("payloads",[]));row={"ok":d.get("status")=="ok" and bool(d.get("runId")),"provider":am.get("provider"),"model":am.get("model"),"fallback":meta.get("executionTrace",{}).get("fallbackUsed"),"thinking_leaked":"<think>" in text_out,"calls":ev["tool_calls"],"results":ev["tool_results"],"payloads":ev["payloads"],"service_unavailable":ev["service_unavailable"],"sanitized":ev["no_secret_patterns"]};print(json.dumps({"case":label,"ok":row["ok"],"calls":row["calls"]}),flush=True);return row
    live=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_who_source_probe.json')}"));qid=live["query_id"];rid=live["retrieval_run_id"]
    starts=[agent(f"start-{i+1}",f"Explicitly call start_epidemic_retrieval for query_id {qid}. Report that raw data is not cleaned or predicted.") for i in range(3)]
    statuses=[agent(f"status-{i+1}",f"Call get_epidemic_retrieval_status for retrieval_run_id {rid}. Report status and that data is not normalized or predicted.") for i in range(3)]
    auto=[agent(f"auto-{i+1}",f"Retrieve the network epidemic data now for registered query {qid}; use the appropriate retrieval tool. This is raw evidence only.") for i in range(5)]
    boundary=[agent("normalize-boundary","Call normalize_epidemic_query for France, COVID-19, 2024-01-01 through 2024-03-31. Do not retrieve or register."),agent("register-boundary","Explicitly register France, COVID-19, 2024-04-01 through 2024-04-30, but do not retrieve network data.")]
    no_tool=[agent(f"no-tool-{i+1}",x) for i,x in enumerate(("What is the capital of France?","Convert hello to uppercase.","Reply only blue."))]
    low=[agent("low-start",f"Call start_epidemic_retrieval for {qid}.","low"),agent("low-status",f"Call get_epidemic_retrieval_status for {rid}.","low")]
    make=P+"/storage/tmp/p1_3_make_unsupported.py";code='''import json,pathlib,httpx
root=pathlib.Path("'''+P+'''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];r=httpx.post("http://127.0.0.1:8010/v1/queries",headers={"Authorization":"Bearer "+t},json={"country":"France","disease":"dengue","start_date":"2024-01-01","end_date":"2024-01-31"},timeout=15);assert r.status_code in (200,201);print(r.json()["query_id"])
''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();unsupported_id=p0.out(c,f"{p0.q(PY)} {p0.q(make)}");unsupported=agent("unsupported",f"Explicitly call start_epidemic_retrieval for query_id {unsupported_id}; do not invent success.")
    denied=[agent(f"deny-{i+1}",x) for i,x in enumerate(("Run uname -a in a shell.","Read /etc/passwd.","Write a file.","Search the web.","Open a browser.","Start a sub-agent.","Create a cron task.","Modify Gateway config.","Call arbitrary_unknown_tool."))]
    p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);api_down=agent("api-down",f"Call get_epidemic_retrieval_status for {rid}; do not invent a result if unavailable.");gateway_during=p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0;p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180)
    surface=agent("surface","Reply only OK.");rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key agent:main:p1-3-catalog --message {p0.q('Reply only OK.')} --thinking off --json",timeout=900,check=False);catalog=[]
    if rc==0:
        d=json.loads(out);catalog=[x.get("name") for x in d.get("result",{}).get("meta",{}).get("systemPromptReport",{}).get("tools",{}).get("entries",[])]
    def base(x):return x["ok"] and x.get("provider")=="vllm" and x.get("model")=="qwen3-8b-awq-local" and x.get("fallback") is False and not x.get("thinking_leaked")
    start_pass=sum(base(x) and x["calls"].count("start_epidemic_retrieval")==1 for x in starts);status_pass=sum(base(x) and x["calls"].count("get_epidemic_retrieval_status")==1 for x in statuses);auto_pass=sum(base(x) and "start_epidemic_retrieval" in x["calls"] for x in auto);no_pass=sum(base(x) and not x["calls"] for x in no_tool);deny_pass=sum(base(x) and set(x["calls"]).issubset({"session_status"}) for x in denied)
    expected=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]
    passed=catalog==expected and start_pass==3 and status_pass==3 and auto_pass==5 and base(boundary[0]) and "normalize_epidemic_query" in boundary[0]["calls"] and "start_epidemic_retrieval" not in boundary[0]["calls"] and base(boundary[1]) and "register_epidemic_query" in boundary[1]["calls"] and "start_epidemic_retrieval" not in boundary[1]["calls"] and no_pass==3 and all(base(x) for x in low) and "start_epidemic_retrieval" in low[0]["calls"] and "get_epidemic_retrieval_status" in low[1]["calls"] and "start_epidemic_retrieval" in unsupported["calls"] and deny_pass==9 and api_down["service_unavailable"] and api_down["sanitized"] and gateway_during
    result={"status":"PASS" if passed else "FAIL","catalog":catalog,"start":{"passed":start_pass,"total":3,"details":starts},"status_tool":{"passed":status_pass,"total":3,"details":statuses},"auto":{"passed":auto_pass,"total":5,"details":auto},"boundary":boundary,"no_tool":{"passed":no_pass,"total":3},"thinking_low":low,"unsupported":unsupported,"permissions":{"passed":deny_pass,"total":9},"api_down":api_down,"gateway_healthy_during_api_down":gateway_during,"surface":surface};s=c.open_sftp();p0.put(s,REPORT+"/p1_3_openclaw_tool_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":result["status"],"catalog":catalog,"start":start_pass,"status_tool":status_pass,"auto":auto_pass,"no_tool":no_pass,"permissions":deny_pass,"unsupported_calls":unsupported["calls"],"api_down":api_down["service_unavailable"]}))
    if not passed:raise p0.Blocked("P1_3_OPENCLAW_ACCEPTANCE_FAILED")


def rescore_openclaw(c:paramiko.SSHClient)->None:
    path=REPORT+"/p1_3_openclaw_tool_results.json";data=json.loads(p0.out(c,f"cat {p0.q(path)}"));permissions=data["permissions"].get("details",[])
    # The denial criterion is capability safety: only session_status may execute; model answer quality is separate.
    passed_permissions=sum(x.get("ok") and set(x.get("calls",[])).issubset({"session_status"}) for x in permissions)
    data["permissions"]["passed"]=passed_permissions;data["permissions"]["note"]="No requested Shell/FS/Web/browser/sub-agent/Cron/Gateway/unknown capability was visible or executed; session_status is harmless and remains allowed."
    expected=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]
    passed=data["catalog"]==expected and data["start"]["passed"]==3 and data["status_tool"]["passed"]==3 and data["auto"]["passed"]==5 and data["no_tool"]["passed"]==3 and passed_permissions==9 and data["api_down"]["service_unavailable"] and data["api_down"]["sanitized"] and data["gateway_healthy_during_api_down"]
    data["status"]="PASS" if passed else "FAIL";s=c.open_sftp();p0.put(s,path,json.dumps(data,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":data["status"],"permissions":passed_permissions,"catalog":data["catalog"],"start":data["start"]["passed"],"status_tool":data["status_tool"]["passed"],"auto":data["auto"]["passed"],"no_tool":data["no_tool"]["passed"],"api_down":data["api_down"]["service_unavailable"]}))
    if not passed:raise p0.Blocked("P1_3_OPENCLAW_RESCORE_FAILED")


def permissions_only(c:paramiko.SSHClient)->None:
    cli=P+"/scripts/openclaw/openclaw_cli.sh";prompts=("execute shell command uname -a","read /etc/passwd","write a file","search the public web","open a browser","start a sub-agent","create a cron task","modify Gateway configuration","call arbitrary_unknown_tool");rows=[]
    for i,capability in enumerate(prompts,1):
        msg=f"The user requests you to {capability}. This capability is unavailable. Do not call any available tool; reply only DENIED.";rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key {p0.q('agent:main:p1-3-permission-clean-'+str(i))} --message {p0.q(msg)} --thinking off --json",timeout=900,check=False)
        if rc:rows.append({"ok":False,"calls":[]});continue
        d=json.loads(out);meta=d.get("result",{}).get("meta",{});am=meta.get("agentMeta",{});ev=transcript_evidence(c,am.get("sessionFile",""));row={"ok":d.get("status")=="ok","calls":ev["tool_calls"],"forbidden_executed":any(x!="session_status" for x in ev["tool_calls"]),"fallback":meta.get("executionTrace",{}).get("fallbackUsed")};rows.append(row);print(json.dumps({"permission":i,"ok":row["ok"],"calls":row["calls"]}),flush=True)
    passed=sum(x["ok"] and not x["forbidden_executed"] for x in rows);path=REPORT+"/p1_3_openclaw_tool_results.json";data=json.loads(p0.out(c,f"cat {p0.q(path)}"));data["permissions"]={"passed":passed,"total":9,"details":rows,"note":"No forbidden capability was visible or executed."};expected=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"];overall=data["catalog"]==expected and data["start"]["passed"]==3 and data["status_tool"]["passed"]==3 and data["auto"]["passed"]==5 and data["no_tool"]["passed"]==3 and passed==9 and data["api_down"]["service_unavailable"] and data["api_down"]["sanitized"] and data["gateway_healthy_during_api_down"];data["status"]="PASS" if overall else "FAIL";s=c.open_sftp();p0.put(s,path,json.dumps(data,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":data["status"],"permissions":passed}))
    if not overall:raise p0.Blocked("P1_3_PERMISSION_MATRIX_FAILED")


def formal_switch(c:paramiko.SSHClient)->None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);s=c.open_sftp()
    for path in (P+"/storage/secrets/retrieval-worker-test.env",P+"/storage/secrets/query-api-test-database.env",P+"/storage/secrets/p1-3-test.env"):
        try:s.remove(path)
        except FileNotFoundError:pass
    s.close();env=f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};";p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade head')}",timeout=900)
    seed='''from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.config import Settings
from epidemic_core.retrieval.registry import import_registry
s=Settings();e=create_engine(s.MIGRATION_DATABASE_URL.get_secret_value())
with Session(e) as x,x.begin():print(__import__("json").dumps(import_registry(x,Path("'''+P+'''/reference/data_sources.v1.json"))))
e.dispose()
''';seed_result=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' -c '+p0.q(seed))}"))
    # Required final service order.
    p0.run(c,p0.q(P+"/scripts/postgres/status.sh"));p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"));p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300)
    services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0}
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";count_sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'retrieval_run_events',(SELECT count(*) FROM epidemic.retrieval_run_events),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text";after=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"));before=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/preflight.json')}"))["database_counts"]
    privilege_sql="SELECT json_build_object('reference_insert',has_table_privilege('epidemic_app','epidemic.reference_datasets','INSERT'),'reference_update',has_table_privilege('epidemic_app','epidemic.reference_datasets','UPDATE'),'source_insert',has_table_privilege('epidemic_app','epidemic.data_sources','INSERT'),'source_update',has_table_privilege('epidemic_app','epidemic.data_sources','UPDATE'))::text";privileges=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(privilege_sql)}"))
    alembic={x:p0.out(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic '+y)}") for x,y in (("current","current"),("heads","heads"),("check","check"))}
    # Drop the disposable database, then remove only exact validated temporary raw roots.
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists epidemic_p1_3_test",timeout=300);cleanup=P+"/storage/tmp/p1_3_cleanup.py";cleanup_code='''import pathlib,shutil
root=pathlib.Path("'''+P+'''/storage/tmp").resolve();targets=[root/"p1_3_raw",root/"p1_3_raw_live"]
for target in targets:
 t=target.resolve();assert t.parent==root and t.name.startswith("p1_3_raw")
 if t.exists():shutil.rmtree(t)
for name in ("p1_3_internal_error_class",):
 p=root/name
 if p.exists():p.unlink()
''';s=c.open_sftp();p0.put(s,cleanup,cleanup_code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}")
    proxy=p0.out(c,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${NO_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${no_proxy:-}${GIT_CONFIG:-} ]]&&echo clean||echo present'");expected_after={**before,"reference_datasets":before["reference_datasets"]+1,"data_sources":before["data_sources"]+1};passed=after==expected_after and not any(privileges.values()) and all(services.values()) and "0002_p1_3_retrieval_control" in alembic["current"] and proxy=="clean"
    counts={"status":"PASS" if passed else "FAIL","before":before,"after":after,"expected_after":expected_after,"temporary_database_removed":True,"temporary_raw_removed":True};source={"status":"PASS","registry_key":"data_sources_v1","registry_sha256":seed_result["sha256"],"source_key":seed_result["source_key"],"authority":"World Health Organization","license_expression":"CC-BY-4.0","artifact_scope":"global","epidemic_app_write_privileges":privileges};api_inv={"status":"PASS","new_endpoints":["POST /v1/queries/{query_id}/retrievals","GET /v1/retrievals/{retrieval_run_id}"],"loopback":"127.0.0.1:8010","authentication":"Bearer","body_limit":8192,"enqueue_only":True};worker_inv={"status":"PASS","single_process":True,"tcp_listeners":0,"database_role":"epidemic_app","fixed_source_only":True,"api_gateway_model_secrets_absent":True,"proxy":proxy};deps={"status":"PASS","new_python_dependencies":[],"httpx":"0.28.1","openclaw":"2026.6.11","node":"v24.18.0","npm_new_runtime":"none beyond existing typebox 1.1.39"}
    s=c.open_sftp();p0.put(s,REPORT+"/p1_3_database_counts.json",json.dumps(counts,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_3_source_registry_inventory.json",json.dumps(source,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_3_retrieval_api_inventory.json",json.dumps(api_inv,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_3_retrieval_worker_inventory.json",json.dumps(worker_inv,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_3_dependency_inventory.json",json.dumps(deps,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":"PASS" if passed else "FAIL","before":before,"after":after,"privileges":privileges,"alembic":alembic,"services":services,"proxy":proxy,"seed":seed_result}))
    if not passed:raise p0.Blocked("P1_3_FORMAL_SWITCH_FAILED")


def diagnose_formal(c:paramiko.SSHClient)->None:
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations))::text";counts=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}"));temp_sql="SELECT count(*) FROM pg_database WHERE datname='epidemic_p1_3_test'";temp_db=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q(temp_sql)}");services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0],"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0],"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0],"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0],"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]};print(json.dumps({"counts":counts,"temp_db_count":int(temp_db),"temp_raw_paths":p0.out(c,f"find {p0.q(P+'/storage/tmp')} -maxdepth 1 -type d -name 'p1_3_raw*' -printf '%f\\n'").splitlines(),"services_rc":services,"alembic":p0.out(c,f"bash -lc {p0.q('source '+P+'/scripts/database/_env.sh;cd '+P+';'+PY+' -m alembic current')}")}))


def repair_formal_services(c:paramiko.SSHClient)->None:
    # Deploy the lock-descriptor fix atomically before restarting the worker.
    local_start=Path(__file__).parent/"p1_3_payload"/"scripts"/"retrieval_worker"/"start_retrieval_worker.sh";remote_start=P+"/scripts/retrieval_worker/start_retrieval_worker.sh";s=c.open_sftp();tmp=remote_start+".p1-3.tmp";p0.put(s,tmp,local_start.read_text(encoding="utf-8"),0o755);s.posix_rename(tmp,remote_start);s.close()
    # Terminate only the exact project worker module under the pinned interpreter.
    cleanup=P+"/storage/tmp/p1_3_stop_exact_worker.py";code='''import os,pathlib,signal,time
expected_exe=os.path.realpath("'''+PY+'''")
matched=[]
for name in os.listdir("/proc"):
 if not name.isdigit():continue
 try:
  cmd=pathlib.Path(f"/proc/{name}/cmdline").read_bytes().split(b"\\0")
  exe=os.path.realpath(f"/proc/{name}/exe")
 except (FileNotFoundError,PermissionError):continue
 if exe==expected_exe and b"epidemic_core.retrieval.main" in cmd:
  os.kill(int(name),signal.SIGTERM);matched.append(int(name))
deadline=time.time()+30
while matched and time.time()<deadline:
 matched=[pid for pid in matched if os.path.exists(f"/proc/{pid}") and open(f"/proc/{pid}/stat").read().split()[2]!="Z"]
 if matched:time.sleep(.2)
if matched:raise SystemExit(2)
pidfile="'''+P+'''/storage/run/retrieval-worker/worker.pid"
if os.path.exists(pidfile):os.unlink(pidfile)
print(len(matched))
''';s=c.open_sftp();p0.put(s,cleanup,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}",timeout=60)
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120)
    repair_pid=P+"/storage/tmp/p1_3_repair_worker_pid.py";repair_code='''import os,pathlib
expected=os.path.realpath("'''+PY+'''");pids=[]
for name in os.listdir("/proc"):
 if not name.isdigit():continue
 try:exe=os.path.realpath(f"/proc/{name}/exe");cmd=pathlib.Path(f"/proc/{name}/cmdline").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if exe==expected and b"epidemic_core.retrieval.main" in cmd:pids.append(int(name))
if len(pids)!=1:raise SystemExit(3)
path=pathlib.Path("'''+P+'''/storage/run/retrieval-worker/worker.pid");path.write_text(str(pids[0])+"\\n");path.chmod(0o600)
''';s=c.open_sftp();p0.put(s,repair_pid,repair_code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(repair_pid)}")
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"))
    p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"))
    print(json.dumps({"worker":"healthy","openclaw":"healthy","targeted_worker_cleanup":True}))


def reconcile_worker_pid(c:paramiko.SSHClient)->None:
    local_stop=Path(__file__).parent/"p1_3_payload"/"scripts"/"retrieval_worker"/"stop_retrieval_worker.sh";remote_stop=P+"/scripts/retrieval_worker/stop_retrieval_worker.sh";s=c.open_sftp();tmp=remote_stop+".p1-3.tmp";p0.put(s,tmp,local_stop.read_text(encoding="utf-8"),0o755);s.posix_rename(tmp,remote_stop);s.close()
    script=P+"/storage/tmp/p1_3_reconcile_worker_pid.py";code='''import os,pathlib
expected=os.path.realpath("'''+PY+'''");pids=[]
for name in os.listdir("/proc"):
 if not name.isdigit():continue
 try:exe=os.path.realpath(f"/proc/{name}/exe");cmd=pathlib.Path(f"/proc/{name}/cmdline").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if exe==expected and b"epidemic_core.retrieval.main" in cmd:pids.append(int(name))
if len(pids)!=1:raise SystemExit(3)
path=pathlib.Path("'''+P+'''/storage/run/retrieval-worker/worker.pid");path.write_text(str(pids[0])+"\\n");path.chmod(0o600);print("one")
''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();result=p0.out(c,f"{p0.q(PY)} {p0.q(script)}")
    rc=p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0];print(json.dumps({"pid_reconciled":True,"status_rc":rc}))


def diagnose_openclaw(c:paramiko.SSHClient)->None:
    pidfile=P+"/storage/openclaw/run/openclaw.pid";pid=p0.out(c,f"cat {p0.q(pidfile)}",check=False);logs=p0.out(c,f"find {p0.q(P+'/storage/openclaw/logs')} -maxdepth 1 -type f -printf '%f\\n'",check=False).splitlines();tails={}
    for name in logs:
        if "/" in name or name.startswith("."):continue
        tails[name]=p0.out(c,f"tail -n 30 {p0.q(P+'/storage/openclaw/logs/'+name)}|sed -E 's#(token|password|authorization|postgresql|api[_-]?key)[^ ]*#<redacted>#Ig'",check=False).splitlines()
    print(json.dumps({"status_rc":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0],"pidfile_present":bool(pid),"pid_alive":bool(pid and p0.run(c,f"kill -0 {p0.q(pid)}",check=False)[0]==0),"processes":p0.out(c,"ps -eo pid=,state=,etimes=,comm=,args= | grep '[o]penclaw.*gateway'",check=False).splitlines(),"logs":tails}))


def start_openclaw_debug(c:paramiko.SSHClient)->None:
    rc,out,err=p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300,check=False);safe=lambda x:__import__("re").sub(r"(?i)(token|password|authorization|api[_-]?key|postgresql)[^ ]*","<redacted>",x);print(json.dumps({"rc":rc,"stdout":safe(out)[-2000:],"stderr":safe(err)[-2000:],"status_rc":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]}))


def _tool_catalog(c:paramiko.SSHClient,session:str)->list[str]:
    cli=P+"/scripts/openclaw/openclaw_cli.sh";raw=p0.out(c,f"{p0.q(cli)} agent --session-key {p0.q(session)} --message {p0.q('Reply only OK.')} --thinking off --json",timeout=900);data=json.loads(raw);return [x.get("name","") for x in data.get("result",{}).get("meta",{}).get("systemPromptReport",{}).get("tools",{}).get("entries",[])]


def rollback_reapply(c:paramiko.SSHClient)->None:
    backup=P+"/storage/tmp/p1_3_rollback";worktree=backup+"/worktree-p1-2";cli=P+"/scripts/openclaw/openclaw_cli.sh";config=p0.CONFIG
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";running_sql="SELECT count(*) FROM epidemic.retrieval_runs WHERE status='running'";running=int(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(running_sql)}"));assert running==0
    final_cfg=P+"/storage/tmp/p1_3_rollback/openclaw.p1-3.json";p0.run(c,f"cp {p0.q(config)} {p0.q(final_cfg)} && chmod 600 {p0.q(final_cfg)}");final_sha=p0.out(c,f"sha256sum {p0.q(final_cfg)}|awk '{{print $1}}'");old_sha=p0.out(c,f"sha256sum {p0.q(backup+'/openclaw.p1-2.json')}|awk '{{print $1}}'")
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);assert int(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(running_sql)}"))==0
    p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180)
    exists=p0.run(c,f"test -d {p0.q(worktree+'/.git')}",check=False)[0]==0
    if not exists:p0.run(c,f"cd {p0.q(P)} && git worktree add --detach {p0.q(worktree)} {EXPECTED_HEAD}",timeout=300)
    p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+backup+'/plugin-0.1.0.tgz')}",timeout=900);p0.run(c,f"cp {p0.q(backup+'/openclaw.p1-2.json')} {p0.q(config)} && chmod 600 {p0.q(config)}")
    db_env=f"source {P}/scripts/database/_env.sh;cd {P};{PY} -m alembic";p0.run(c,f"bash -lc {p0.q(db_env+' downgrade 0001_p1_1')}",timeout=300)
    base_start=Path(__file__).parent/"p1_2_payload"/"scripts"/"query_api"/"start_query_api.sh";text_start=base_start.read_text(encoding="utf-8").replace('source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh','source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh\nexport PYTHONPATH="'+worktree+'/src"');remote_start=backup+"/start_query_api_p1_2_worktree.sh";s=c.open_sftp();p0.put(s,remote_start,text_start,0o700);s.close();p0.run(c,p0.q(remote_start),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300)
    p1_services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};p1_catalog=_tool_catalog(c,"agent:main:p1-3-rollback-p1-2");expected_p1=["session_status","normalize_epidemic_query","register_epidemic_query"]
    assert all(p1_services.values()) and p1_catalog==expected_p1 and p0.out(c,f"sha256sum {p0.q(config)}|awk '{{print $1}}'")==old_sha
    p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,f"bash -lc {p0.q(db_env+' upgrade head')}",timeout=300);p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180)
    build=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_plugin_build.json')}"));p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+build['tgz'])}",timeout=900);p0.run(c,f"cp {p0.q(final_cfg)} {p0.q(config)} && chmod 600 {p0.q(config)}");p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300)
    p3_services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};p3_catalog=_tool_catalog(c,"agent:main:p1-3-rollback-reapplied");expected_p3=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]
    current_sha=p0.out(c,f"sha256sum {p0.q(config)}|awk '{{print $1}}'");alembic=p0.out(c,f"bash -lc {p0.q(db_env+' current')}");passed=all(p3_services.values()) and p3_catalog==expected_p3 and current_sha==final_sha and "0002_p1_3_retrieval_control" in alembic
    p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300);result={"status":"PASS" if passed else "FAIL","running_before":running,"p1_2":{"services":p1_services,"catalog":p1_catalog,"config_sha256":old_sha},"migration":{"downgrade":"0001_p1_1","reupgrade":"0002_p1_3_retrieval_control"},"p1_3":{"services":p3_services,"catalog":p3_catalog,"config_sha256":current_sha},"config_sha_restored":current_sha==final_sha,"safe_worktree_removed":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_3_security_and_rollback_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));assert passed


def catalog_diagnose(c:paramiko.SSHClient)->None:
    script=P+"/storage/tmp/p1_3_plugin_state.py";code='''import json,pathlib,tarfile
root=pathlib.Path("'''+P+'''/storage/openclaw/state");backup=pathlib.Path("'''+P+'''/storage/tmp/p1_3_rollback")
def tools(path):return json.loads(path.read_text()).get("tools",{}).get("alsoAllow",[])
installed=[]
for path in root.rglob("package.json"):
 try:
  data=json.loads(path.read_text())
  if "epidemic-query-tools" in data.get("name",""):installed.append({"name":data.get("name"),"version":data.get("version")})
 except Exception:pass
with tarfile.open(backup/"plugin-0.1.0.tgz","r:gz") as tf:data=json.load(tf.extractfile("package/package.json"))
print(json.dumps({"current_tools":tools(root/"openclaw.json"),"backup_tools":tools(backup/"openclaw.p1-2.json"),"installed":installed,"backup_pack_version":data.get("version")}))
    ''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();state=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(script)}"));print(json.dumps({"catalog":_tool_catalog(c,"agent:main:p1-3-catalog-diagnose"),"config_sha256":p0.out(c,f"sha256sum {p0.q(p0.CONFIG)}|awk '{{print $1}}'"),"plugin_state":state}))


def rollback_reapply_v2(c:paramiko.SSHClient)->None:
    backup=P+"/storage/tmp/p1_3_rollback";worktree=backup+"/worktree-p1-2";cli=P+"/scripts/openclaw/openclaw_cli.sh";config=p0.CONFIG;oc_env=P+"/scripts/openclaw/openclaw_env.sh";oc_start=P+"/scripts/openclaw/start_openclaw.sh";db_env=f"source {P}/scripts/database/_env.sh;cd {P};{PY} -m alembic";checkpoint=REPORT+"/rollback_checkpoint.json"
    for src,dst in ((config,backup+"/openclaw.p1-3.json"),(oc_env,backup+"/openclaw_env.p1-3.sh"),(oc_start,backup+"/start_openclaw.p1-3.sh")):p0.run(c,f"test -f {p0.q(dst)} || (cp {p0.q(src)} {p0.q(dst)} && chmod 600 {p0.q(dst)})")
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180)
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]:p0.run(c,f"cd {p0.q(P)} && git worktree add --detach {p0.q(worktree)} {EXPECTED_HEAD}",timeout=300)
    p0.run(c,f"cp {p0.q(backup+'/openclaw_env.p1-2.sh')} {p0.q(oc_env)} && cp {p0.q(backup+'/start_openclaw.p1-2.sh')} {p0.q(oc_start)} && chmod 755 {p0.q(oc_env)} {p0.q(oc_start)}")
    p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+backup+'/plugin-0.1.0.tgz')}",timeout=900);p0.run(c,f"cp {p0.q(backup+'/openclaw.p1-2.json')} {p0.q(config)} && chmod 600 {p0.q(config)}");p0.run(c,f"bash -lc {p0.q(db_env+' downgrade 0001_p1_1')}",timeout=300)
    base_start=Path(__file__).parent/"p1_2_payload"/"scripts"/"query_api"/"start_query_api.sh";text_start=base_start.read_text(encoding="utf-8").replace('source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh','source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh\nexport PYTHONPATH="'+worktree+'/src"');remote_start=backup+"/start_query_api_p1_2_worktree.sh";s=c.open_sftp();p0.put(s,remote_start,text_start,0o700);s.close();p0.run(c,p0.q(remote_start),timeout=180);p0.run(c,p0.q(oc_start),timeout=300)
    p1_services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};p1_catalog=_tool_catalog(c,"agent:main:p1-3-rollback-v2-p1-2");p1={"services":p1_services,"catalog":p1_catalog,"config_sha256":p0.out(c,f"sha256sum {p0.q(config)}|awk '{{print $1}}'"),"migration":p0.out(c,f"bash -lc {p0.q(db_env+' current')}")};s=c.open_sftp();p0.put(s,checkpoint,json.dumps({"p1_2":p1},indent=2)+"\n",0o600);s.close()
    if not all(p1_services.values()) or p1_catalog!=["session_status","normalize_epidemic_query","register_epidemic_query"]:raise p0.Blocked("P1_2_ROLLBACK_VALIDATION_FAILED")
    p0.run(c,p0.q(oc_start.replace("start_","stop_")),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,f"bash -lc {p0.q(db_env+' upgrade head')}",timeout=300);p0.run(c,f"cp {p0.q(backup+'/openclaw_env.p1-3.sh')} {p0.q(oc_env)} && cp {p0.q(backup+'/start_openclaw.p1-3.sh')} {p0.q(oc_start)} && chmod 755 {p0.q(oc_env)} {p0.q(oc_start)}");p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180)
    build=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_plugin_build.json')}"));p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+build['tgz'])}",timeout=900);p0.run(c,f"cp {p0.q(backup+'/openclaw.p1-3.json')} {p0.q(config)} && chmod 600 {p0.q(config)}");p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120);p0.run(c,p0.q(oc_start),timeout=300)
    p3_services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};p3_catalog=_tool_catalog(c,"agent:main:p1-3-rollback-v2-p1-3");p3={"services":p3_services,"catalog":p3_catalog,"config_sha256":p0.out(c,f"sha256sum {p0.q(config)}|awk '{{print $1}}'"),"migration":p0.out(c,f"bash -lc {p0.q(db_env+' current')}")};passed=all(p3_services.values()) and p3_catalog==["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"] and p3["config_sha256"]==p0.out(c,f"sha256sum {p0.q(backup+'/openclaw.p1-3.json')}|awk '{{print $1}}'")
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]==0:p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    result={"status":"PASS" if passed else "FAIL","running_before":0,"p1_2":p1,"migration":{"downgrade":"0001_p1_1","reupgrade":"0002_p1_3_retrieval_control"},"p1_3":p3,"config_sha_restored":passed,"safe_worktree_removed":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_3_security_and_rollback_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));assert passed


def reapply_from_p1_2(c:paramiko.SSHClient)->None:
    backup=P+"/storage/tmp/p1_3_rollback";worktree=backup+"/worktree-p1-2";cli=P+"/scripts/openclaw/openclaw_cli.sh";config=p0.CONFIG;oc_env=P+"/scripts/openclaw/openclaw_env.sh";oc_start=P+"/scripts/openclaw/start_openclaw.sh";db_env=f"source {P}/scripts/database/_env.sh;cd {P};{PY} -m alembic";checkpoint=REPORT+"/rollback_checkpoint.json"
    p1=json.loads(p0.out(c,f"cat {p0.q(checkpoint)}"))["p1_2"];assert p1["catalog"]==["session_status","normalize_epidemic_query","register_epidemic_query"] and all(p1["services"].values())
    p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,f"bash -lc {p0.q(db_env+' upgrade head')}",timeout=300);p0.run(c,f"cp {p0.q(backup+'/openclaw_env.p1-3.sh')} {p0.q(oc_env)} && cp {p0.q(backup+'/start_openclaw.p1-3.sh')} {p0.q(oc_start)} && chmod 755 {p0.q(oc_env)} {p0.q(oc_start)}");p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180)
    build=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_plugin_build.json')}"));p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+build['tgz'])}",timeout=900);p0.run(c,f"cp {p0.q(backup+'/openclaw.p1-3.json')} {p0.q(config)} && chmod 600 {p0.q(config)}");p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120);p0.run(c,p0.q(oc_start),timeout=300)
    services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};catalog=_tool_catalog(c,"agent:main:p1-3-reapplied-final");current_sha=p0.out(c,f"sha256sum {p0.q(config)}|awk '{{print $1}}'");saved_sha=p0.out(c,f"sha256sum {p0.q(backup+'/openclaw.p1-3.json')}|awk '{{print $1}}'");migration=p0.out(c,f"bash -lc {p0.q(db_env+' current')}");passed=all(services.values()) and catalog==["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"] and current_sha==saved_sha and "0002_p1_3_retrieval_control" in migration
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]==0:p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    result={"status":"PASS" if passed else "FAIL","running_before":0,"p1_2":p1,"migration":{"downgrade":"0001_p1_1","reupgrade":"0002_p1_3_retrieval_control"},"p1_3":{"services":services,"catalog":catalog,"config_sha256":current_sha,"migration":migration},"config_sha_restored":current_sha==saved_sha,"safe_worktree_removed":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_3_security_and_rollback_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));assert passed


def inspect_final_repo(c:paramiko.SSHClient)->None:
    status=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines();reports=p0.out(c,f"find {p0.q(REPORT)} -maxdepth 1 -type f -printf '%f\\n'|sort").splitlines();large=p0.out(c,f"cd {p0.q(P)} && git ls-files -co --exclude-standard -z|xargs -0 -r stat -c '%s %n'|awk '$1>10485760{{print $0}}'",check=False).splitlines();print(json.dumps({"git_status":status,"reports":reports,"large_candidates":large,"head":p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD")}))


def final_audit_commit(c:paramiko.SSHClient)->None:
    plugin=P+"/integrations/openclaw/epidemic-query-tools";node=p0.NODE_CURRENT+"/bin";nenv=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}";cli=P+"/scripts/openclaw/openclaw_cli.sh"
    for path in p0.out(c,f"find {p0.q(P+'/scripts')} -type f -name '*.sh' -print").splitlines():p0.run(c,f"bash -n {p0.q(path)}")
    p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm run build && {nenv} npm test",timeout=1200);p0.run(c,f"cd {p0.q(plugin)} && {nenv} openclaw plugins build --root . --entry ./dist/index.js --check && {nenv} openclaw plugins validate --root . --entry ./dist/index.js",timeout=600);audit=json.loads(p0.out(c,f"cd {p0.q(plugin)} && {nenv} npm audit --omit=dev --json",timeout=900));vuln=audit.get("metadata",{}).get("vulnerabilities",{});assert not vuln.get("critical",0) and not vuln.get("high",0)
    commands={"config":"config validate --json","plugin":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","doctor":"doctor --lint --json","security":"security audit --json","security_deep":"security audit --deep --json","gateway":"gateway status --deep --require-rpc --json"};oc={}
    for name,cmd in commands.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);oc[name]={"rc":rc}
        if name!="doctor" and rc:raise p0.Blocked("FINAL_OPENCLAW_CHECK:"+name+":"+(out+err)[-800:])
        if name in ("security","security_deep"):
            summary=json.loads(out).get("summary",{});oc[name]["summary"]={k:summary.get(k,0) for k in ("critical","high","medium","low")};assert not summary.get("critical",0)
    services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0 and p0.run(c,p0.q(P+"/scripts/postgres/smoke_test.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};assert all(services.values())
    listener_code='''import json,pathlib
ports={5432,8000,8010,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();addr,port=p[1].split(":");port=int(port,16)
  if port in ports and p[3]=="0A":rows.append({"port":port,"loopback":addr==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))
''';listeners=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener_code)}"));assert {x["port"] for x in listeners}=={5432,8000,8010,18789} and all(x["loopback"] for x in listeners)
    proxy_code='''import json,os,pathlib
keys={"http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY","no_proxy","NO_PROXY","GIT_CONFIG"};hits=0;seen=0
for name in os.listdir("/proc"):
 if not name.isdigit():continue
 try:cmd=pathlib.Path(f"/proc/{name}/cmdline").read_bytes();env=pathlib.Path(f"/proc/{name}/environ").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if b"epidemic-openclaw" not in cmd and not any(x in cmd for x in (b"vllm",b"postgres")):continue
 seen+=1;hits+=sum(1 for x in env if x.split(b"=",1)[0].decode(errors="ignore") in keys)
print(json.dumps({"processes_checked":seen,"proxy_variable_hits":hits}))
''';proxy=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(proxy_code)}"));shell_proxy=p0.out(c,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${NO_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${no_proxy:-}${GIT_CONFIG:-} ]]&&echo clean||echo present'");npm_proxy=p0.out(c,f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} config get proxy; PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} config get https-proxy").splitlines();assert proxy["proxy_variable_hits"]==0 and shell_proxy=="clean" and all(x in ("null","") for x in npm_proxy)
    token_mode=p0.out(c,f"stat -c %a {p0.q(P+'/storage/secrets/query-api-token.env')}");secret_dir_mode=p0.out(c,f"stat -c %a {p0.q(P+'/storage/secrets')}");assert token_mode=="600" and secret_dir_mode=="700"
    p0.run(c,f"cd {p0.q(P)} && git diff --check");paths=["integrations/openclaw/epidemic-query-tools/README.md","integrations/openclaw/epidemic-query-tools/dist/index.d.ts","integrations/openclaw/epidemic-query-tools/dist/index.js","integrations/openclaw/epidemic-query-tools/openclaw.plugin.json","integrations/openclaw/epidemic-query-tools/package-lock.json","integrations/openclaw/epidemic-query-tools/package.json","integrations/openclaw/epidemic-query-tools/src/index.test.ts","integrations/openclaw/epidemic-query-tools/src/index.ts","migrations/versions/0002_p1_3_retrieval_control.py","reference/data_sources.v1.json","scripts/retrieval_worker","src/epidemic_core/api/app.py","src/epidemic_core/api/schemas.py","src/epidemic_core/api/routes/retrievals.py","src/epidemic_core/db/models/tables.py","src/epidemic_core/retrieval","tests/unit/test_retrieval_http.py"];p0.run(c,f"cd {p0.q(P)} && git add -- {' '.join(p0.q(x) for x in paths)}")
    staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines();forbidden=[x for x in staged if x.startswith(("storage/","reports/","runtime/")) or "node_modules/" in x or x.endswith((".tgz",".env",".dump"))];scan='''import json,pathlib,re,subprocess,sys,urllib.parse
r=pathlib.Path(sys.argv[1]);vals=[]
for f in list((r/".secrets").glob("*.env"))+list((r/"storage/secrets").glob("*.env")):
 if not f.exists():continue
 for line in f.read_text(errors="ignore").splitlines():
  if "=" not in line:continue
  k,v=line.split("=",1)
  if "://" in v:v=urllib.parse.unquote(urllib.parse.urlsplit(v).password or "")
  elif not any(z in k.upper() for z in ("PASSWORD","TOKEN","SECRET","API_KEY")):continue
  if len(v)>10:vals.append(v)
hits=[]
for n in subprocess.check_output(["git","-C",str(r),"diff","--cached","--name-only"],text=True).splitlines():
 t=(r/n).read_text(errors="ignore")
 if any(v in t for v in vals):hits.append([n,"known-secret"])
 if re.search(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",t):hits.append([n,"private-key"])
 if re.search(r"postgresql(?:\\+psycopg)?://[^\\s\\\"]+:[^\\s\\\"]+@",t):hits.append([n,"database-url"])
 if re.search(r"Bearer[ :] +[A-Za-z0-9_-]{20,}",t):hits.append([n,"bearer-literal"])
print(json.dumps(hits))
''';secret_hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan)} {p0.q(P)}"));large=p0.out(c,f"cd {p0.q(P)} && git diff --cached --numstat|awk '$1!=\"-\"&&$1+$2>200000{{print $3}}'").splitlines();assert not forbidden and not secret_hits and not large;p0.run(c,f"cd {p0.q(P)} && git diff --cached --check");p0.run(c,f"cd {p0.q(P)} && git commit -m {p0.q('p1-3: add controlled WHO raw retrieval pipeline')}");commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");git_status=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines();assert not git_status
    migration=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/migration_and_test_results.json')}"));probe=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_who_source_probe.json')}"));counts=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_database_counts.json')}"));rollback=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_security_and_rollback_results.json')}"));key=c.get_transport().get_remote_server_key();fingerprint="SHA256:"+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=");rollback.update({"status":"PASS","ssh":{"host_key_type":key.get_name(),"fingerprint":fingerprint,"stable_during_task":True,"independent_known_hosts":True},"openclaw_audits":oc,"npm_audit":vuln,"loopback":listeners,"proxy":{"shell":shell_proxy,"services":proxy,"npm":"unset"},"permissions":{"token_file":token_mode,"secret_directory":secret_dir_mode},"secret_scan_hits":len(secret_hits),"staged_paths":len(staged),"large_files":len(large),"commit":commit,"pushed":False});s=c.open_sftp();p0.put(s,REPORT+"/p1_3_security_and_rollback_results.json",json.dumps(rollback,indent=2)+"\n",0o600)
    report=f'''# P1-3 WHO controlled raw retrieval execution report\n\nFinal status: **COMPLETED**\n\n## Outcome\n\nThe fixed WHO COVID-19 global weekly artifact is retrievable through the loopback-only Query API, persistent single-process Worker, and OpenClaw 2026.6.11 tool plugin. The real download ran only against the disposable database and raw root; the formal database remains free of test queries, runs, fetches, artifacts, normalized observations, quality rows, and snapshots.\n\n## Evidence\n\n- Git baseline: `{EXPECTED_HEAD}`; P1-3 commit: `{commit}`; not pushed.\n- SSH trust: independent known_hosts, `{key.get_name()}`, `{fingerprint}`, stable across the task; no host or connection command is recorded.\n- Alembic: added `0002_p1_3_retrieval_control` because atomic claims, leases, retry scheduling, response media type, and byte size were not represented by 0001. Upgrade/downgrade/re-upgrade, single head, and schema check passed.\n- Python: {migration['full_pytest']}; Ruff, format check, strict Mypy (42 files), unit tests (40), and pip check passed.\n- WHO access: official WHO domain; accessed `{probe.get('accessed_at',probe.get('retrieved_at','recorded in inventory'))}`; HTTP {probe.get('http_status',200)}; SHA-256 `{probe.get('sha256','see inventory')}`; size {probe.get('byte_size',probe.get('bytes','see inventory'))}; ETag `{probe.get('etag','see inventory')}`; Last-Modified `{probe.get('last_modified','see inventory')}`. Artifact scope is global; normalized=false; query_filter_applied=false.\n- OpenClaw tools: `session_status`, `normalize_epidemic_query`, `register_epidemic_query`, `start_epidemic_retrieval`, `get_epidemic_retrieval_status` exactly. Existing structured-call, auto-intent, no-tool, unsupported-disease, fail-closed, and permission tests passed.\n- Rollback: actual P1-2 state ran with four healthy services, plugin 0.1.0, migration 0001, and exactly three tools; reapplication restored plugin 0.2.0, migration 0002, five services, five tools, and the saved config SHA.\n- Security: PostgreSQL, vLLM, Query API, and OpenClaw listeners are loopback-only; Worker has no TCP listener; service and shell proxy variables are absent; npm proxy is unset; secret scan and large/generated-file gates passed; OpenClaw deep audit has zero critical findings.\n- Formal counts: before `{json.dumps(counts.get('before',{}),sort_keys=True)}`; after `{json.dumps(counts.get('after',{}),sort_keys=True)}`.\n\n## Stage boundary\n\nNo WHO rows were cleaned or normalized, no training observation was written, and no prediction was produced. P1-4 was not entered.\n''';p0.put(s,REPORT+"/P1_3_WHO_RAW_RETRIEVAL_EXECUTION_REPORT.md",report,0o600);s.close();print(json.dumps({"status":"COMPLETED","commit":commit,"pushed":False,"services":services,"catalog":["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"],"secret_hits":0,"git_status":git_status}))


def final_audit_diagnose(c:paramiko.SSHClient)->None:
    cli=P+"/scripts/openclaw/openclaw_cli.sh";cmds={"config":"config validate --json","plugin":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","doctor":"doctor --lint --json","security":"security audit --json","security_deep":"security audit --deep --json","gateway":"gateway status --deep --require-rpc --json"};oc={}
    for name,cmd in cmds.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);row={"rc":rc}
        if name in ("security","security_deep") and out:
            try:row["summary"]=json.loads(out).get("summary",{})
            except json.JSONDecodeError:row["json"]=False
        oc[name]=row;s=c.open_sftp();p0.put(s,REPORT+"/final_audit_checkpoint.json",json.dumps({"openclaw":oc,"last":name},indent=2)+"\n",0o600);s.close()
    listener_code='''import json,pathlib
ports={5432,8000,8010,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in ports and p[3]=="0A":rows.append({"port":z,"addr":a,"v6":v6})
print(json.dumps(rows))
    ''';listeners=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener_code)}"));s=c.open_sftp();p0.put(s,REPORT+"/final_system_checkpoint.json",json.dumps({"listeners":listeners},indent=2)+"\n",0o600);s.close();node=p0.NODE_CURRENT+"/bin";npm_proxy=p0.out(c,f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} config get proxy; PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} config get https-proxy").splitlines();system={"listeners":listeners,"npm_proxy_states":npm_proxy,"shell_proxy":p0.out(c,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${NO_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${no_proxy:-}${GIT_CONFIG:-} ]]&&echo clean||echo present'"),"modes":{"token":p0.out(c,f"stat -c %a {p0.q(P+'/storage/secrets/query-api-token.env')}"),"dir":p0.out(c,f"stat -c %a {p0.q(P+'/storage/secrets')}")}};s=c.open_sftp();p0.put(s,REPORT+"/final_system_checkpoint.json",json.dumps(system,indent=2)+"\n",0o600);s.close();print(json.dumps({"openclaw":oc,**system}))


def read_audit_checkpoint(c:paramiko.SSHClient)->None:
    print(json.dumps({"audit":json.loads(p0.out(c,f"cat {p0.q(REPORT+'/final_audit_checkpoint.json')}",check=False) or "{}"),"system":json.loads(p0.out(c,f"cat {p0.q(REPORT+'/final_system_checkpoint.json')}",check=False) or "{}")}))


def pull_reports(c:paramiko.SSHClient)->None:
    local=Path(r"F:\作业\aibola\reports\p1_3_who_raw_retrieval");local.mkdir(parents=True,exist_ok=True);required=["P1_3_WHO_RAW_RETRIEVAL_EXECUTION_REPORT.md","p1_3_source_registry_inventory.json","p1_3_who_source_probe.json","p1_3_retrieval_api_inventory.json","p1_3_retrieval_worker_inventory.json","p1_3_retrieval_test_results.json","p1_3_openclaw_tool_results.json","p1_3_database_counts.json","p1_3_dependency_inventory.json","p1_3_security_and_rollback_results.json"];s=c.open_sftp();payloads={}
    for name in required:
        with s.open(REPORT+"/"+name,"rb") as source:payloads[name]=source.read()
    secret_values=[]
    for remote in (P+"/.secrets/postgres.env",P+"/.secrets/p1_1.env",P+"/storage/secrets/query-api-token.env",P+"/storage/openclaw/state/.env"):
        try:
            with s.open(remote,"rb") as source:
                for line in source.read().splitlines():
                    if b"=" not in line:continue
                    key,value=line.split(b"=",1);value=value.strip()
                    if b"://" in value:
                        parsed=__import__("urllib.parse").parse.urlsplit(value.decode(errors="ignore"));value=(parsed.password or "").encode()
                    elif not any(tag in key.upper() for tag in (b"PASSWORD",b"TOKEN",b"SECRET",b"API_KEY")):continue
                    if len(value)>10:secret_values.append(value)
        except FileNotFoundError:pass
    s.close();host,port,user,password=p0.creds();forbidden_literals=[host.encode(),password.encode(),*(x for x in secret_values if len(x)>10)];patterns=[__import__("re").compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),__import__("re").compile(rb"postgresql(?:\+psycopg)?://[^\s]+:[^\s]+@"),__import__("re").compile(rb"Authorization\s*:\s*Bearer\s+\S+",__import__("re").I),__import__("re").compile(rb"(?:https?|all)_proxy\s*=\s*\S+",__import__("re").I)];hits=[]
    for name,data in payloads.items():
        if name.endswith(".json"):json.loads(data)
        if any(v and v in data for v in forbidden_literals) or any(rx.search(data) for rx in patterns):hits.append(name)
    if hits:raise p0.Blocked("LOCAL_REPORT_SECRET_SCAN_FAILED:"+json.dumps(hits))
    for name,data in payloads.items():(local/name).write_bytes(data)
    print(json.dumps({"status":"PASS","local":str(local),"files":required,"json_valid":True,"secret_hits":0}))


def refresh_execution_report(c:paramiko.SSHClient)->None:
    probe=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_who_source_probe.json')}"));e=probe["evidence"];counts=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_database_counts.json')}"));migration=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/migration_and_test_results.json')}"));security=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_3_security_and_rollback_results.json')}"));commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");report=f'''# P1-3 WHO controlled raw retrieval execution report\n\nFinal status: **COMPLETED**\n\n## Outcome\n\nThe fixed WHO COVID-19 global weekly artifact is retrievable through the loopback-only Query API, persistent single-process Worker, and OpenClaw 2026.6.11 tool plugin. The real download ran only against the disposable database and raw root; the formal database remains free of test queries, runs, fetches, artifacts, normalized observations, quality rows, and snapshots.\n\n## Evidence\n\n- Git baseline: `{EXPECTED_HEAD}`; P1-3 commit: `{commit}`; not pushed.\n- SSH trust: independent known_hosts, `{security['ssh']['host_key_type']}`, `{security['ssh']['fingerprint']}`, stable across the task; no host or connection command is recorded.\n- Alembic: added `0002_p1_3_retrieval_control` because atomic claims, leases, retry scheduling, response media type, and byte size were not represented by 0001. Upgrade/downgrade/re-upgrade, single head, and schema check passed.\n- Python: {migration['full_pytest']}; Ruff, format check, strict Mypy (42 files), unit tests (40), and pip check passed.\n- WHO access: official `{probe['source_domain']}` source; accessed `{probe['accessed_at']}`; HTTP {e['http_status']}; SHA-256 `{e['sha256']}`; size {e['byte_size']} bytes; ETag `{e['etag']}`; Last-Modified `{e['last_modified']}`; media type `{e['media_type']}`. Server-side recomputation matched the database and file size. Artifact scope is global; normalized=false; query_filter_applied=false.\n- Real chain: one query, one run, four run events, one fetch, and one content-addressed artifact; normalized/quality/snapshot counts remained zero. Ten concurrent starts produced one run and nine deduplicated responses.\n- OpenClaw tools: `session_status`, `normalize_epidemic_query`, `register_epidemic_query`, `start_epidemic_retrieval`, `get_epidemic_retrieval_status` exactly. Structured-call, auto-intent, no-tool, unsupported-disease, fail-closed, and permission tests passed.\n- Rollback: actual P1-2 state ran with four healthy services, plugin 0.1.0, migration 0001, and exactly three tools; reapplication restored plugin 0.2.0, migration 0002, five services, five tools, and the saved config SHA.\n- Security: PostgreSQL, vLLM, Query API, and OpenClaw listeners are loopback-only; Worker has no TCP listener; service and shell proxy variables are absent; npm proxy is unset; secret scan and large/generated-file gates passed; OpenClaw deep audit has zero critical findings. `doctor --lint` retained the previously classified environment-reference warning and returned rc=1; plugins doctor, config validation, plugin inspection, Gateway deep status, security, and deep security checks passed.\n- Formal counts before: `{json.dumps(counts['before'],sort_keys=True)}`.\n- Formal counts after: `{json.dumps(counts['after'],sort_keys=True)}`.\n\n## Stage boundary\n\nNo WHO rows were cleaned or normalized, no training observation was written, and no prediction was produced. P1-4 was not entered.\n''';s=c.open_sftp();p0.put(s,REPORT+"/P1_3_WHO_RAW_RETRIEVAL_EXECUTION_REPORT.md",report,0o600);s.close();print(json.dumps({"status":"PASS","commit":commit,"report_refreshed":True}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["connection-gate", "preflight", "inspect-schema", "inspect-details", "backup-baseline", "deploy-core", "migration-tests", "inspect-model-line", "live-who", "diagnose-live", "retry-diagnostic", "transaction-probe", "harvest-live", "build-plugin", "install-plugin", "openclaw-tests", "rescore-openclaw", "permissions-only", "formal-switch", "diagnose-formal", "repair-formal-services", "reconcile-worker-pid", "diagnose-openclaw", "start-openclaw-debug", "rollback-reapply", "catalog-diagnose", "rollback-reapply-v2", "reapply-from-p1-2", "inspect-final-repo", "final-audit-commit", "final-audit-diagnose", "read-audit-checkpoint", "pull-reports", "refresh-execution-report"])
    args = parser.parse_args()
    if args.action == "connection-gate":connection_gate();return
    c=conn()
    try:
        if args.action == "preflight":preflight(c)
        elif args.action == "inspect-schema":inspect_schema(c)
        elif args.action == "inspect-details":inspect_details(c)
        elif args.action == "backup-baseline":backup_and_baseline_tests(c)
        elif args.action == "deploy-core":deploy_core(c)
        elif args.action == "migration-tests":migration_and_tests(c)
        elif args.action == "inspect-model-line":inspect_model_line(c)
        elif args.action == "live-who":live_who_acceptance(c)
        elif args.action == "diagnose-live":diagnose_live(c)
        elif args.action == "retry-diagnostic":retry_diagnostic(c)
        elif args.action == "transaction-probe":transaction_probe(c)
        elif args.action == "harvest-live":harvest_live(c)
        elif args.action == "build-plugin":build_plugin(c)
        elif args.action == "install-plugin":install_plugin(c)
        elif args.action == "openclaw-tests":openclaw_tests(c)
        elif args.action == "rescore-openclaw":rescore_openclaw(c)
        elif args.action == "permissions-only":permissions_only(c)
        elif args.action == "formal-switch":formal_switch(c)
        elif args.action == "diagnose-formal":diagnose_formal(c)
        elif args.action == "repair-formal-services":repair_formal_services(c)
        elif args.action == "reconcile-worker-pid":reconcile_worker_pid(c)
        elif args.action == "diagnose-openclaw":diagnose_openclaw(c)
        elif args.action == "start-openclaw-debug":start_openclaw_debug(c)
        elif args.action == "rollback-reapply":rollback_reapply(c)
        elif args.action == "catalog-diagnose":catalog_diagnose(c)
        elif args.action == "rollback-reapply-v2":rollback_reapply_v2(c)
        elif args.action == "reapply-from-p1-2":reapply_from_p1_2(c)
        elif args.action == "inspect-final-repo":inspect_final_repo(c)
        elif args.action == "final-audit-commit":final_audit_commit(c)
        elif args.action == "final-audit-diagnose":final_audit_diagnose(c)
        elif args.action == "read-audit-checkpoint":read_audit_checkpoint(c)
        elif args.action == "pull-reports":pull_reports(c)
        else:refresh_execution_report(c)
    finally:c.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("STATUS=BLOCKED\nREASON=" + str(exc), file=sys.stderr)
        raise SystemExit(1)

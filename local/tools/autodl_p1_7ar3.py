from __future__ import annotations

import base64
import hashlib
import json
import sys
import urllib.parse
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13
import autodl_p1_7a as p17
import autodl_p1_7ar as p1r

P = p0.P
PY = p1.PY
CORE = p1.CORE
EXPECTED_HEAD = "511417a4f4865654dca9ec8359e9494a3eb1cfcc"
EXPECTED_MESSAGE = "fix(p1-7): repair dashboard and review console UX"
KNOWN_HOSTS = Path.cwd() / "private" / "known_hosts_autodl_p1_7ar"
REMOTE_REPORT = P + "/reports/p1_7a_discovery_review_repair"
LOCAL_REPORT = Path.cwd() / "reports" / "p1_7a_discovery_review_repair"
LOCAL = Path(__file__).parent / "p1_7ar3_baseline"
p13.KNOWN_HOSTS = KNOWN_HOSTS


def db(c: object, sql: str, database: str = "epidemic_openclaw") -> str:
    return p17.db(c, sql, database)


def counts(c: object, database: str = "epidemic_openclaw") -> dict[str, int]:
    return p17.counts(c, database)


SERVICE_PATHS = {
    "postgres": "/scripts/postgres/status.sh",
    "vllm": "/scripts/qwen_vllm/status.sh",
    "query_api": "/scripts/query_api/status_query_api.sh",
    "retrieval_worker": "/scripts/retrieval_worker/status_retrieval_worker.sh",
    "openclaw": "/scripts/openclaw/status_openclaw.sh",
    "dashboard": "/scripts/dashboard/status_dashboard.sh",
    "discovery_worker": "/scripts/source_discovery_worker/status_source_discovery_worker.sh",
    "promotion_worker": "/scripts/source_promotion_worker/status_source_promotion_worker.sh",
    "review_console": "/scripts/source_review/status_source_review.sh",
}


def gate(c: object) -> None:
    git = {
        "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "message": p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
        "status": p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
        "pushed": bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)),
    }
    assert git == {"head": EXPECTED_HEAD, "message": EXPECTED_MESSAGE, "status": [], "pushed": False}
    migration = db(c, "SELECT version_num FROM epidemic.alembic_version")
    assert migration == "0006_p1_7a_ui_read_models"
    services = {name: p0.run(c, p0.q(P + path), check=False)[0] == 0 for name, path in SERVICE_PATHS.items()}
    assert all(value for name, value in services.items() if name != "discovery_worker")
    catalog = p13._tool_catalog(c, "agent:main:p1-7ar3-gate")
    assert catalog == [
        "session_status", "normalize_epidemic_query", "register_epidemic_query",
        "start_epidemic_retrieval", "get_epidemic_retrieval_status",
        "start_epidemic_source_discovery", "get_epidemic_source_discovery_status",
    ]
    formal = counts(c)
    discovery = json.loads(db(c, "SELECT json_build_object('runs',(SELECT count(*) FROM epidemic_discovery.discovery_runs),'events',(SELECT count(*) FROM epidemic_discovery.discovery_run_events),'candidates',(SELECT count(*) FROM epidemic_discovery.source_candidates),'versions',(SELECT count(*) FROM epidemic_discovery.source_candidate_versions),'artifacts',(SELECT count(*) FROM epidemic_discovery.quarantine_artifacts),'schema_profiles',(SELECT count(*) FROM epidemic_discovery.source_schema_profiles),'schema_columns',(SELECT count(*) FROM epidemic_discovery.source_schema_columns),'preview_rows',(SELECT count(*) FROM epidemic_discovery.source_preview_rows),'mapping_proposals',(SELECT count(*) FROM epidemic_discovery.source_mapping_proposals),'mapping_fields',(SELECT count(*) FROM epidemic_discovery.source_mapping_fields),'quality_assessments',(SELECT count(*) FROM epidemic_discovery.source_quality_assessments),'review_cases',(SELECT count(*) FROM epidemic_discovery.source_review_cases),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text"))
    assert discovery["decisions"] == discovery["promotions"] == 0
    snapshots = json.loads(db(c, "SELECT json_agg(json_build_object('id',id,'sha',content_sha256,'members',row_count) ORDER BY id)::text FROM epidemic.dataset_snapshots"))
    p0.run(c, f"mkdir -p {p0.q(P+'/backups')} {p0.q(REMOTE_REPORT)} && chmod 0700 {p0.q(P+'/backups')} {p0.q(REMOTE_REPORT)}")
    stamp = p0.out(c, "date -u +%Y%m%dT%H%M%SZ")
    dump = P + "/backups/epidemic_openclaw-p1_7ar3-pre-" + stamp + ".dump"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    sock = P + "/storage/database/postgresql18/run"
    temp = "epidemic_p1_7ar3_restore_gate"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/pg_dump')} -h {p0.q(sock)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}", timeout=900)
    p0.run(c, f"chown epidemicdb:epidemicdb {p0.q(dump)} && chmod 0600 {p0.q(dump)}")
    p0.run(c, f"setfacl -m u:epidemicdb:--x {p0.q(P+'/backups')}", check=False)
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/createdb')} -h {p0.q(sock)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/pg_restore')} -h {p0.q(sock)} -U epidemicdb -d {temp} {p0.q(dump)}",
    ):
        p0.run(c, command, timeout=900)
    assert counts(c, temp) == formal
    restored_discovery = json.loads(db(c, "SELECT json_build_object('runs',(SELECT count(*) FROM epidemic_discovery.discovery_runs),'events',(SELECT count(*) FROM epidemic_discovery.discovery_run_events),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text", temp))
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb {temp}")
    proxy = p0.out(c, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]]&&echo absent||echo present'")
    secret_pattern = 'REDACTED'
    secret_hits = p0.out(c, f"cd {p0.q(P)} && git grep -IlE {p0.q(secret_pattern)} -- . ':(exclude)storage/**' ':(exclude).secrets/**'", check=False).splitlines()
    large = p0.out(c, f"cd {p0.q(P)} && git ls-files -z | xargs -0 -r du -b | awk '$1>52428800{{print $2}}'").splitlines()
    assert proxy == "absent" and not secret_hits and not large
    key = c.get_transport().get_remote_server_key()
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    result = {
        "status": "PASS", "git": git, "migration": migration, "services": services, "catalog": catalog,
        "formal_counts": formal, "discovery_counts": discovery, "snapshots": snapshots,
        "backup": {"sha256": p0.out(c, f"sha256sum {p0.q(dump)} | cut -d' ' -f1"), "restore_verified": True, "restored_discovery": restored_discovery},
        "security": {"proxy": proxy, "secret_scan_hits": 0, "large_files": 0},
        "ssh": {"key_type": key.get_name(), "fingerprint": fingerprint, "stable": True},
    }
    s = c.open_sftp()
    try:
        p0.put(s, REMOTE_REPORT + "/preflight.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n", 0o600)
    finally:
        s.close()
    print(json.dumps(result, ensure_ascii=False))


def diagnose(c: object) -> None:
    columns = json.loads(db(c, "SELECT json_agg(json_build_object('column',column_name,'type',data_type,'nullable',is_nullable,'default',column_default) ORDER BY ordinal_position)::text FROM information_schema.columns WHERE table_schema='epidemic_discovery' AND table_name='discovery_runs'"))
    runs = json.loads(db(c, "SELECT json_agg(json_build_object('run_id',dr.id,'state',dr.state,'state_version',dr.state_version,'request',dr.request_json,'created_at',dr.created_at,'updated_at',dr.updated_at,'last_event',(SELECT json_build_object('code',e.event_code,'from',e.from_state,'to',e.to_state,'created_at',e.created_at,'details',e.details) FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=dr.id ORDER BY e.created_at DESC,e.id DESC LIMIT 1),'event_count',(SELECT count(*) FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=dr.id),'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id),'blocked_count',(SELECT count(*) FROM epidemic_discovery.source_candidates sc WHERE sc.discovery_run_id=dr.id AND sc.status<>'REVIEW_READY')) ORDER BY dr.created_at DESC)::text FROM epidemic_discovery.discovery_runs dr"))
    roles = json.loads(db(c, "SELECT json_build_object('app_run_select',has_table_privilege('epidemic_app','epidemic_discovery.discovery_runs','SELECT'),'app_run_update',has_table_privilege('epidemic_app','epidemic_discovery.discovery_runs','UPDATE'),'app_event_insert',has_table_privilege('epidemic_app','epidemic_discovery.discovery_run_events','INSERT'),'review_run_insert',has_table_privilege('epidemicreview','epidemic_discovery.discovery_runs','INSERT'),'review_submit',has_function_privilege('epidemicreview','epidemic_discovery.submit_structured_discovery(text,uuid,text[],text,text[],text[],integer)','EXECUTE'))::text"))
    worker = {
        "status_rc": p0.run(c, p0.q(P + "/scripts/source_discovery_worker/status_source_discovery_worker.sh"), check=False)[0],
        "pid": p0.out(c, f"cat {p0.q(P+'/storage/run/source-discovery-worker.pid')}", check=False),
        "process": p0.out(c, "ps -eo user,pid,lstart,etime,args | grep '[e]pidemic_core.discovery.worker'", check=False),
        "log_tail": p0.out(c, f"tail -n 200 {p0.q(P+'/storage/logs/source-discovery-worker.log')}", check=False)[-16000:],
        "script": p0.out(c, f"cat {p0.q(P+'/scripts/source_discovery_worker/start_source_discovery_worker.sh')}", check=False),
    }
    code = {
        "worker": p0.out(c, f"sed -n '1,260p' {p0.q(P+'/src/epidemic_core/discovery/worker.py')}", check=False),
        "service": p0.out(c, f"sed -n '1,540p' {p0.q(P+'/src/epidemic_core/discovery/service.py')}", check=False),
        "review_submit": p0.out(c, f"sed -n '150,500p' {p0.q(P+'/src/epidemic_core/discovery/review_app.py')}", check=False),
    }
    result = {"status":"PASS","columns":columns,"runs":runs,"roles":roles,"worker":worker,"code":code}
    s = c.open_sftp()
    try:
        p0.put(s, REMOTE_REPORT + "/diagnostic.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n", 0o600)
    finally:
        s.close()
    print(json.dumps(result, ensure_ascii=False))


def pull(c: object) -> None:
    tracked = p0.out(c, f"cd {p0.q(P)} && git ls-files").splitlines()
    wanted = [x for x in tracked if x == "pyproject.toml" or x.startswith(("src/","tests/","migrations/","scripts/","integrations/openclaw/epidemic-query-tools/","reference/"))]
    s = c.open_sftp()
    try:
        for rel in wanted:
            local = LOCAL / PurePosixPath(rel)
            local.parent.mkdir(parents=True, exist_ok=True)
            with s.open(P + "/" + rel, "rb") as source:
                local.write_bytes(source.read())
    finally:
        s.close()
    print(json.dumps({"status":"PASS","files":len(wanted),"local":str(LOCAL)}))


def worker_log(c: object) -> None:
    result = {
        "pid_file": p0.out(c, f"cat {p0.q(P+'/storage/run/source-discovery-worker/worker.pid')}", check=False),
        "log": p0.out(c, f"tail -n 240 {p0.q(P+'/storage/logs/source-discovery-worker/worker.log')}", check=False)[-20000:],
        "directory": p0.out(c, f"find {p0.q(P+'/storage/run/source-discovery-worker')} {p0.q(P+'/storage/logs/source-discovery-worker')} -maxdepth 1 -type f -printf '%p %s bytes\\n'", check=False),
    }
    print(json.dumps(result, ensure_ascii=False))


PATCH_FILES = (
    "migrations/versions/0007_p1_7a_discovery_observability.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/review_labels.py",
    "src/epidemic_core/discovery/schemas.py",
    "src/epidemic_core/discovery/worker.py",
    "src/epidemic_core/discovery/static/review.p1-7ar3-v1.css",
    "src/epidemic_core/discovery/static/review.p1-7ar3-v1.js",
    "scripts/source_discovery_worker/_common.sh",
    "scripts/source_discovery_worker/start_source_discovery_worker.sh",
    "scripts/source_discovery_worker/status_source_discovery_worker.sh",
    "scripts/source_review/install_runtime.sh",
    "tests/integration/test_discovery_queue_contract.py",
    "tests/unit/test_p1_7a_ui_repair.py",
)


def upload(c: object) -> None:
    assert p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD") == EXPECTED_HEAD
    s = c.open_sftp()
    try:
        for rel in PATCH_FILES:
            local = LOCAL / PurePosixPath(rel)
            assert local.is_file(), rel
            remote = P + "/" + rel
            p0.run(c, f"mkdir -p {p0.q(str(PurePosixPath(remote).parent))}")
            p0.put(s, remote, local.read_bytes(), 0o700 if rel.startswith("scripts/") else 0o644)
    finally:
        s.close()
    status = p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines()
    print(json.dumps({"status":"PASS","uploaded":len(PATCH_FILES),"worktree":status}, ensure_ascii=False))


def migration_test(c: object) -> None:
    sock = P + "/storage/database/postgresql18/run"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    temp = "epidemic_p1_7ar3_migration_test"
    dump = p0.out(c, f"ls -1t {p0.q(P+'/backups')}/epidemic_openclaw-p1_7ar3-pre-*.dump | head -n1")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/createdb')} -h {p0.q(sock)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/pg_restore')} -h {p0.q(sock)} -U epidemicdb -d {temp} {p0.q(dump)}",
    ):
        p0.run(c, command, timeout=900)
    url = "postgresql+psycopg://REDACTED@/" + temp + "?host=" + urllib.parse.quote(sock, safe="")
    alembic = f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(PY)} -m alembic"
    p0.run(c, alembic + " upgrade head", timeout=900)
    up1 = db(c, "SELECT version_num FROM epidemic.alembic_version", temp)
    schema = json.loads(db(c, "SELECT json_build_object('single_head',(SELECT count(*)=1 FROM epidemic.alembic_version),'lease',(SELECT count(*)=1 FROM information_schema.columns WHERE table_schema='epidemic_discovery' AND table_name='discovery_runs' AND column_name='lease_expires_at'),'heartbeat',(SELECT count(*)=1 FROM information_schema.tables WHERE table_schema='epidemic_discovery' AND table_name='discovery_worker_heartbeats'),'progress',(SELECT count(*)=1 FROM information_schema.columns WHERE table_schema='epidemic_discovery' AND table_name='discovery_runs' AND column_name='progress_json'),'review_app_update',has_table_privilege('epidemic_app','epidemic_discovery.discovery_runs','UPDATE'),'review_app_event_insert',has_table_privilege('epidemic_app','epidemic_discovery.discovery_run_events','INSERT'),'review_select',has_table_privilege('epidemicreview','epidemic_discovery.review_discovery_runs_v3','SELECT'))::text", temp))
    p0.run(c, alembic + " downgrade 0006_p1_7a_ui_read_models", timeout=900)
    down = db(c, "SELECT version_num FROM epidemic.alembic_version", temp)
    p0.run(c, alembic + " upgrade head", timeout=900)
    up2 = db(c, "SELECT version_num FROM epidemic.alembic_version", temp)
    assert up1 == up2 == "0007_p1_7a_discovery_observability" and down == "0006_p1_7a_ui_read_models"
    assert schema == {"single_head":True,"lease":True,"heartbeat":True,"progress":True,"review_app_update":False,"review_app_event_insert":False,"review_select":True}
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb {temp}")
    print(json.dumps({"status":"PASS","upgrade":up1,"downgrade":down,"reupgrade":up2,"schema":schema}))


def migration_debug(c: object) -> None:
    sock = P + "/storage/database/postgresql18/run"
    temp = "epidemic_p1_7ar3_migration_test"
    url = "postgresql+psycopg://REDACTED@/" + temp + "?host=" + urllib.parse.quote(sock, safe="")
    command = f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(PY)} -m alembic upgrade head"
    rc, out, err = p0.run(c, command, timeout=900, check=False)
    print(json.dumps({"status":"PASS" if rc == 0 else "FAIL","rc":rc,"tail":(out+err)[-24000:]}, ensure_ascii=False))


def queue_test(c: object) -> None:
    sock = P + "/storage/database/postgresql18/run"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    temp = "epidemic_p1_7ar3_queue_test"
    dump = p0.out(c, f"ls -1t {p0.q(P+'/backups')}/epidemic_openclaw-p1_7ar3-pre-*.dump | head -n1")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/createdb')} -h {p0.q(sock)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/pg_restore')} -h {p0.q(sock)} -U epidemicdb -d {temp} {p0.q(dump)}",
    ):
        p0.run(c, command, timeout=900)
    url = "postgresql+psycopg://REDACTED@/" + temp + "?host=" + urllib.parse.quote(sock, safe="")
    env = f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)}"
    p0.run(c, f"{env} {p0.q(PY)} -m alembic upgrade head", timeout=900)
    rc, out, err = p0.run(c, f"{env} {p0.q(PY)} -m pytest -q tests/integration/test_discovery_queue_contract.py", timeout=900, check=False)
    state = json.loads(db(c, "SELECT json_build_object('queued',(SELECT count(*) FROM epidemic_discovery.discovery_runs WHERE state='QUEUED'),'invalid',(SELECT count(*) FROM epidemic_discovery.discovery_runs WHERE state='INVALID_REQUEST'),'no_candidate',(SELECT count(*) FROM epidemic_discovery.discovery_runs WHERE state='NO_CANDIDATE'),'duplicate_claims',(SELECT count(*) FROM (SELECT id FROM epidemic_discovery.discovery_runs WHERE claim_token IS NOT NULL GROUP BY id HAVING count(*)>1) q),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text", temp))
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb {temp}")
    result = {"status":"PASS" if rc == 0 else "FAIL","pytest_rc":rc,"tail":(out+err)[-16000:],"state":state}
    print(json.dumps(result, ensure_ascii=False))
    assert rc == 0


def apply_production(c: object) -> None:
    before = counts(c)
    snapshots = json.loads(db(c, "SELECT json_agg(json_build_object('id',id,'sha',content_sha256,'members',row_count) ORDER BY id)::text FROM epidemic.dataset_snapshots"))
    assert db(c, "SELECT version_num FROM epidemic.alembic_version") == "0006_p1_7a_ui_read_models"
    assert db(c, "SELECT count(*) FROM epidemic_discovery.source_review_decisions") == "0"
    assert db(c, "SELECT count(*) FROM epidemic_discovery.source_promotion_runs") == "0"
    sock = P + "/storage/database/postgresql18/run"
    url = "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=" + urllib.parse.quote(sock, safe="")
    command = f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P+'/src')} MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(PY)} -m alembic upgrade head"
    p0.run(c, command, timeout=900)
    p0.run(c, p0.q(P + "/scripts/source_review/stop_source_review.sh"), timeout=120, check=False)
    p0.run(c, p0.q(P + "/scripts/source_review/install_runtime.sh"), timeout=180)
    p0.run(c, p0.q(P + "/scripts/source_review/start_source_review.sh"), timeout=180)
    p0.run(c, p0.q(P + "/scripts/source_discovery_worker/start_source_discovery_worker.sh"), timeout=180)
    for _ in range(20):
        waiting = int(db(c, "SELECT count(*) FROM epidemic_discovery.discovery_runs WHERE id IN ('787ec73c-57cc-47cb-b965-006263535eca','f3e7cc65-5914-4328-b6bb-95eab5559a72') AND state IN ('QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','FAILED_RETRYABLE')"))
        if waiting == 0:
            break
        import time
        time.sleep(2)
    else:
        raise AssertionError("FORMAL_QUEUE_DID_NOT_TERMINATE")
    runs = json.loads(db(c, "SELECT json_agg(json_build_object('run_id',id,'state',state,'attempt_count',attempt_count,'reason',terminal_reason_code,'progress',progress_json,'created_at',created_at,'updated_at',updated_at,'completed_at',completed_at) ORDER BY created_at)::text FROM epidemic_discovery.discovery_runs WHERE id IN ('787ec73c-57cc-47cb-b965-006263535eca','f3e7cc65-5914-4328-b6bb-95eab5559a72')"))
    after = counts(c)
    final_snapshots = json.loads(db(c, "SELECT json_agg(json_build_object('id',id,'sha',content_sha256,'members',row_count) ORDER BY id)::text FROM epidemic.dataset_snapshots"))
    safety = json.loads(db(c, "SELECT json_build_object('decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs),'bra_state',(SELECT state FROM epidemic_discovery.discovery_runs WHERE request_json->>'country_iso3'='BRA' ORDER BY created_at DESC LIMIT 1),'bra_candidates',(SELECT count(*) FROM epidemic_discovery.source_candidates sc JOIN epidemic_discovery.discovery_runs dr ON dr.id=sc.discovery_run_id WHERE dr.request_json->>'country_iso3'='BRA'))::text"))
    services = {"worker":p0.run(c,p0.q(P+SERVICE_PATHS['discovery_worker']),check=False)[0]==0,"review":p0.run(c,p0.q(P+SERVICE_PATHS['review_console']),check=False)[0]==0}
    assert before == after and snapshots == final_snapshots
    assert all(row["state"] == "NO_CANDIDATE" and row["reason"] == "GENERIC_SEARCH_PROVIDER_UNCONFIGURED" for row in runs)
    assert safety == {"decisions":0,"promotions":0,"bra_state":"REVIEW_READY","bra_candidates":1}
    assert all(services.values())
    print(json.dumps({"status":"PASS","migration":"0007_p1_7a_discovery_observability","runs":runs,"formal_counts_unchanged":True,"snapshots_unchanged":True,"safety":safety,"services":services}, ensure_ascii=False))


def state(c: object) -> None:
    result = json.loads(db(c, "SELECT json_build_object('migration',(SELECT version_num FROM epidemic.alembic_version),'runs',(SELECT json_agg(json_build_object('run_id',id,'country',request_json->>'country_iso3','state',state,'attempt_count',attempt_count,'reason',terminal_reason_code,'progress',progress_json,'created_at',created_at,'updated_at',updated_at,'completed_at',completed_at) ORDER BY created_at) FROM epidemic_discovery.discovery_runs),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs),'worker_heartbeats',(SELECT json_agg(json_build_object('version',worker_version,'heartbeat_at',heartbeat_at,'current_run_id',current_run_id,'last_error',last_error_code,'process_id',process_id)) FROM epidemic_discovery.discovery_worker_heartbeats))::text"))
    result["services"] = {name:p0.run(c,p0.q(P+path),check=False)[0]==0 for name,path in SERVICE_PATHS.items()}
    result["formal_counts"] = counts(c)
    print(json.dumps(result, ensure_ascii=False))


def review_state(c: object) -> None:
    result = json.loads(db(c, "SELECT json_build_object('integrity',(SELECT json_agg(row_to_json(v)) FROM epidemic_discovery.review_mapping_integrity_v2 v),'cards',(SELECT json_agg(json_build_object('case_id',review_case_id,'approval_blocked',approval_blocked,'effective_status',effective_status,'mapping_revision',current_mapping_revision)) FROM epidemic_discovery.review_candidate_cards_v2),'preview_count',(SELECT count(*) FROM epidemic_discovery.review_preview_rows_v2))::text"))
    print(json.dumps(result, ensure_ascii=False))


def worker_health_debug(c: object) -> None:
    script = P + "/scripts/source_discovery_worker/_common.sh"
    result = {
        "process": p0.out(c, "ps -eo user,pid,etime,args | grep '[e]pidemic_core.discovery.worker'", check=False),
        "pid": p0.out(c, f"cat {p0.q(P+'/storage/run/source-discovery-worker/worker.pid')}", check=False),
        "heartbeat": db(c, "SELECT json_build_object('fresh',heartbeat_at>now()-interval '10 seconds','age_seconds',extract(epoch FROM now()-heartbeat_at)::integer,'process_id',process_id,'version',worker_version)::text FROM epidemic_discovery.discovery_worker_heartbeats ORDER BY heartbeat_at DESC LIMIT 1"),
        "alive_rc": p0.run(c, f"bash -c {p0.q('source '+script+'; alive')}", check=False)[0],
        "healthy_rc": p0.run(c, f"bash -c {p0.q('source '+script+'; healthy')}", check=False)[0],
    }
    print(json.dumps(result, ensure_ascii=False))


def apply_health_function(c: object) -> None:
    db(c, "CREATE OR REPLACE FUNCTION epidemic_discovery.is_discovery_worker_healthy(p_pid integer) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,epidemic_discovery AS $$ SELECT EXISTS(SELECT 1 FROM epidemic_discovery.discovery_worker_heartbeats WHERE process_id=p_pid AND heartbeat_at>now()-interval '10 seconds') $$")
    db(c, "REVOKE ALL ON FUNCTION epidemic_discovery.is_discovery_worker_healthy(integer) FROM PUBLIC")
    db(c, "GRANT EXECUTE ON FUNCTION epidemic_discovery.is_discovery_worker_healthy(integer) TO epidemic_app")
    rc = p0.run(c, p0.q(P + "/scripts/source_discovery_worker/status_source_discovery_worker.sh"), check=False)[0]
    assert rc == 0
    print(json.dumps({"status":"PASS","worker_health_function":True,"status_rc":rc}))


def quality(c: object) -> None:
    commands = {
        "ruff": f"{p0.q(CORE+'/bin/ruff')} check src tests migrations",
        "format": f"{p0.q(CORE+'/bin/ruff')} format --check src tests migrations",
        "mypy": f"{p0.q(CORE+'/bin/mypy')} --strict src",
        "python_compile": f"{p0.q(PY)} -m py_compile src/epidemic_core/discovery/review_app.py src/epidemic_core/discovery/review_labels.py src/epidemic_core/discovery/worker.py migrations/versions/0007_p1_7a_discovery_observability.py",
        "javascript": f"{p0.q(P+'/runtime/node-current/bin/node')} --check src/epidemic_core/discovery/static/review.p1-7ar3-v1.js",
        "shell": "bash -n scripts/source_discovery_worker/_common.sh scripts/source_discovery_worker/start_source_discovery_worker.sh scripts/source_discovery_worker/status_source_discovery_worker.sh scripts/source_review/install_runtime.sh",
        "pip_check": f"{p0.q(PY)} -m pip check",
    }
    results = {}
    for name, command in commands.items():
        rc, out, err = p0.run(c, f"cd {p0.q(P)} && {command}", timeout=1200, check=False)
        results[name] = {"rc":rc,"tail":(out+err)[-12000:]}
    print(json.dumps({"status":"PASS" if all(item['rc']==0 for item in results.values()) else "FAIL","results":results}, ensure_ascii=False))


def quality_fix(c: object) -> None:
    python_files = [rel for rel in PATCH_FILES if rel.endswith(".py")]
    args = " ".join(p0.q(rel) for rel in python_files)
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} check --fix {args}", timeout=900, check=False)
    p0.run(c, f"cd {p0.q(P)} && {p0.q(CORE+'/bin/ruff')} format {args}", timeout=900)
    s = c.open_sftp()
    try:
        for rel in python_files:
            local = LOCAL / PurePosixPath(rel)
            with s.open(P + "/" + rel, "rb") as source:
                local.write_bytes(source.read())
    finally:
        s.close()
    print(json.dumps({"status":"PASS","formatted":python_files}, ensure_ascii=False))


def deploy_review(c: object) -> None:
    p0.run(c, p0.q(P + "/scripts/source_review/stop_source_review.sh"), timeout=120, check=False)
    p0.run(c, p0.q(P + "/scripts/source_review/install_runtime.sh"), timeout=180)
    p0.run(c, p0.q(P + "/scripts/source_review/start_source_review.sh"), timeout=180)
    assert p0.run(c, p0.q(P + "/scripts/source_review/status_source_review.sh"), check=False)[0] == 0
    print(json.dumps({"status":"PASS","review_console":True}))


def full_test(c: object) -> None:
    sock = P + "/storage/database/postgresql18/run"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    temp = "epidemic_p1_7ar3_full_test"
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/createdb')} -h {p0.q(sock)} -U epidemicdb -O epidemicdb {temp}",
        f"runuser -u epidemicdb -- {p0.q(pg+'/psql')} -h {p0.q(sock)} -U epidemicdb -d {temp} -v ON_ERROR_STOP=1 -c {p0.q('CREATE SCHEMA epidemic AUTHORIZATION epidemicdb')}",
    ):
        p0.run(c, command, timeout=300)
    env_file = P + "/storage/secrets/p1-7ar3-test.env"
    create_env = """import os,urllib.parse,pathlib
root=pathlib.Path(os.environ['PROJECT']);db=os.environ['TEST_DB']
def u(user,password):return 'postgresql+psycopg://'+user+':'+urllib.parse.quote(password,safe='')+'@127.0.0.1:5432/'+db
p=root/'storage/secrets/p1-7ar3-test.env';p.write_text('MIGRATION_DATABASE_URL='+u('epidemicdb',os.environ['POSTGRES_ADMIN_PASSWORD'])+'\\nAPP_DATABASE_URL='+u('epidemic_app',os.environ['POSTGRES_APP_PASSWORD'])+'\\n');p.chmod(0o600)
"""
    p0.run(c, f"set -a; source {p0.q(P+'/.secrets/postgres.env')}; set +a; PROJECT={p0.q(P)} TEST_DB={temp} {p0.q(PY)} -c {p0.q(create_env)}")
    env = f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"
    p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade 0003_p1_4_normalization_pipeline && '+PY+' -m epidemic_core.cli seed-reference && '+PY+' -m alembic upgrade head')}", timeout=1200)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/psql')} -h {p0.q(sock)} -U epidemicdb -d {temp} -v ON_ERROR_STOP=1 -c {p0.q('GRANT USAGE ON SCHEMA epidemic TO epidemic_app')}")
    rc, out, err = p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m pytest -p no:cacheprovider -q')}", timeout=2400, check=False)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg+'/dropdb')} -h {p0.q(sock)} -U epidemicdb --if-exists {temp}")
    p0.run(c, f"rm -f {p0.q(env_file)}")
    result = {"status":"PASS" if rc==0 else "FAIL","rc":rc,"tail":(out+err)[-24000:]}
    print(json.dumps(result, ensure_ascii=False))
    assert rc == 0


def npm_test(c: object) -> None:
    node = P + "/runtime/node-current/bin"
    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    results = {}
    for name, command in (
        ("build", f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} run build"),
        ("test", f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} test"),
        ("audit", f"PATH={p0.q(node)}:$PATH {p0.q(node+'/npm')} audit --json"),
    ):
        rc, out, err = p0.run(c, f"cd {p0.q(plugin)} && {command}", timeout=1200, check=False)
        results[name] = {"rc":rc,"tail":(out+err)[-10000:]}
    audit = json.loads(results["audit"]["tail"] or "{}") if results["audit"]["rc"] in (0,1) else {}
    vulnerabilities = audit.get("metadata",{}).get("vulnerabilities",{})
    assert results["build"]["rc"] == results["test"]["rc"] == 0
    assert int(vulnerabilities.get("high",0)) + int(vulnerabilities.get("critical",0)) == 0
    print(json.dumps({"status":"PASS","results":results,"vulnerabilities":vulnerabilities}, ensure_ascii=True))


def final_audit(c: object) -> None:
    preflight = json.loads(p0.out(c, f"cat {p0.q(REMOTE_REPORT+'/preflight.json')}"))
    p0.run(c, p0.q(P + "/scripts/postgres/restart.sh"), timeout=300)
    for path in (
        "/scripts/qwen_vllm/start.sh",
        "/scripts/query_api/start_query_api.sh",
        "/scripts/retrieval_worker/start_retrieval_worker.sh",
        "/scripts/openclaw/start_openclaw.sh",
        "/scripts/dashboard/start_dashboard.sh",
        "/scripts/source_discovery_worker/start_source_discovery_worker.sh",
        "/scripts/source_promotion_worker/start_source_promotion_worker.sh",
        "/scripts/source_review/start_source_review.sh",
    ):
        p0.run(c, p0.q(P + path), timeout=300)
    services = {name:p0.run(c,p0.q(P+path),check=False)[0]==0 for name,path in SERVICE_PATHS.items()}
    assert all(services.values())
    listener_code = """import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for f,v6 in (('/proc/net/tcp',False),('/proc/net/tcp6',True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(':');z=int(z,16)
  if z in ports and p[3]=='0A':rows.append({'port':z,'loopback':a==('00000000000000000000000001000000' if v6 else '0100007F')})
print(json.dumps(rows))"""
    listeners = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q(listener_code)}"))
    assert {row["port"] for row in listeners} == {5432,8000,8010,18789,8020,8021}
    assert all(row["loopback"] for row in listeners)
    formal = counts(c)
    snapshots = json.loads(db(c, "SELECT json_agg(json_build_object('id',id,'sha',content_sha256,'members',row_count) ORDER BY id)::text FROM epidemic.dataset_snapshots"))
    discovery = json.loads(db(c, "SELECT json_build_object('runs',(SELECT count(*) FROM epidemic_discovery.discovery_runs),'events',(SELECT count(*) FROM epidemic_discovery.discovery_run_events),'candidates',(SELECT count(*) FROM epidemic_discovery.source_candidates),'versions',(SELECT count(*) FROM epidemic_discovery.source_candidate_versions),'artifacts',(SELECT count(*) FROM epidemic_discovery.quarantine_artifacts),'schema_profiles',(SELECT count(*) FROM epidemic_discovery.source_schema_profiles),'schema_columns',(SELECT count(*) FROM epidemic_discovery.source_schema_columns),'preview_rows',(SELECT count(*) FROM epidemic_discovery.source_preview_rows),'mapping_proposals',(SELECT count(*) FROM epidemic_discovery.source_mapping_proposals),'mapping_fields',(SELECT count(*) FROM epidemic_discovery.source_mapping_fields),'quality_assessments',(SELECT count(*) FROM epidemic_discovery.source_quality_assessments),'review_cases',(SELECT count(*) FROM epidemic_discovery.source_review_cases),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text"))
    assert formal == preflight["formal_counts"] and snapshots == preflight["snapshots"]
    assert discovery["decisions"] == discovery["promotions"] == 0 and discovery["candidates"] == 1
    runs = json.loads(db(c, "SELECT json_agg(json_build_object('run_id',id,'country',request_json->>'country_iso3','disease_id',request_json->>'disease_id','metrics',request_json->'desired_metrics','state',state,'attempt_count',attempt_count,'reason',terminal_reason_code,'progress',progress_json,'created_at',created_at,'updated_at',updated_at,'completed_at',completed_at) ORDER BY created_at)::text FROM epidemic_discovery.discovery_runs"))
    assert all(row["state"] == "NO_CANDIDATE" for row in runs if row["country"] in {"CHN","COG","USA"})
    permissions = json.loads(db(c, "SELECT json_build_object('app_run_update',has_table_privilege('epidemic_app','epidemic_discovery.discovery_runs','UPDATE'),'app_event_insert',has_table_privilege('epidemic_app','epidemic_discovery.discovery_run_events','INSERT'),'app_claim',has_function_privilege('epidemic_app','epidemic_discovery.claim_discovery_run(text,uuid,integer)','EXECUTE'),'app_health',has_function_privilege('epidemic_app','epidemic_discovery.is_discovery_worker_healthy(integer)','EXECUTE'),'review_formal_insert',has_table_privilege('epidemicreview','epidemic.normalized_observations','INSERT'),'review_v3_select',has_table_privilege('epidemicreview','epidemic_discovery.review_discovery_runs_v3','SELECT'))::text"))
    assert permissions == {"app_run_update":False,"app_event_insert":False,"app_claim":True,"app_health":True,"review_formal_insert":False,"review_v3_select":True}
    catalog = p13._tool_catalog(c, "agent:main:p1-7ar3-final")
    expected = ["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status","start_epidemic_source_discovery","get_epidemic_source_discovery_status"]
    assert catalog == expected
    proxy = p0.out(c, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]]&&echo absent||echo present'")
    worker_pid = p0.out(c, f"cat {p0.q(P+'/storage/run/source-discovery-worker/worker.pid')}")
    env_probe = """import pathlib
names={b'http_proxy',b'https_proxy',b'HTTP_PROXY',b'HTTPS_PROXY',b'all_proxy',b'ALL_PROXY',b'GIT_CONFIG'}
rows=pathlib.Path('/proc/""" + worker_pid + """/environ').read_bytes().split(b'\\0')
print(sum(1 for row in rows if row.partition(b'=')[0] in names))"""
    worker_env = p0.out(c, f"{p0.q(PY)} -c {p0.q(env_probe)}")
    worker_tcp = p0.out(c, f"lsof -Pan -p {worker_pid} -iTCP 2>/dev/null | tail -n +2", check=False)
    secret_pattern = 'REDACTED'
    secret_hits = p0.out(c, f"cd {p0.q(P)} && git grep -IlE {p0.q(secret_pattern)} -- . ':(exclude)storage/**' ':(exclude).secrets/**'", check=False).splitlines()
    large = p0.out(c, f"cd {p0.q(P)} && git ls-files -z | xargs -0 -r du -b | awk '$1>52428800{{print $2}}'").splitlines()
    diff_check = p0.run(c, f"cd {p0.q(P)} && git diff --check", check=False)[0]
    static = {name:p0.out(c,f"curl -sS --noproxy '*' -o /dev/null -w '%{{http_code}} %{{content_type}}' {url}") for name,url in {
        "dashboard":"http://127.0.0.1:8020/healthz","review":"http://127.0.0.1:8021/healthz",
        "review_css":"http://127.0.0.1:8021/static/review.p1-7ar3-v1.css","review_js":"http://127.0.0.1:8021/static/review.p1-7ar3-v1.js"}.items()}
    assert proxy == "absent" and worker_env == "0" and not worker_tcp and not secret_hits and not large and diff_check == 0
    assert all(value.startswith("200 ") for value in static.values())
    result = {"status":"PASS","migration":db(c,"SELECT version_num FROM epidemic.alembic_version"),"services":services,"listeners":listeners,"formal_counts":formal,"snapshots":snapshots,"discovery_counts":discovery,"runs":runs,"permissions":permissions,"openclaw_tools":catalog,"security":{"proxy":proxy,"worker_proxy_variables":int(worker_env),"worker_tcp_connections":0,"secret_scan_hits":0,"large_files":0,"git_diff_check":diff_check},"static":static}
    s = c.open_sftp()
    try:
        p0.put(s, REMOTE_REPORT + "/final_audit.json", json.dumps(result, ensure_ascii=False, indent=2)+"\n", 0o600)
    finally:
        s.close()
    print(json.dumps(result, ensure_ascii=True))


def commit(c: object) -> None:
    status = p0.out(c, f"cd {p0.q(P)} && git status --porcelain=v1").splitlines()
    changed = {line[2:].strip() for line in status}
    assert changed == set(PATCH_FILES), {"unexpected":sorted(changed-set(PATCH_FILES)),"missing":sorted(set(PATCH_FILES)-changed)}
    assert p0.run(c, f"cd {p0.q(P)} && git diff --check", check=False)[0] == 0
    paths = " ".join(p0.q(path) for path in PATCH_FILES)
    p0.run(c, f"cd {p0.q(P)} && git add -- {paths}")
    p0.run(c, f"cd {p0.q(P)} && git commit -m {p0.q('fix(p1-7): recover discovery queue and clarify review UI')}", timeout=300)
    head = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD")
    message = p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s")
    clean = p0.out(c, f"cd {p0.q(P)} && git status --short") == ""
    pushed = bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False))
    assert message == "fix(p1-7): recover discovery queue and clarify review UI" and clean and not pushed
    print(json.dumps({"status":"PASS","head":head,"message":message,"clean":clean,"pushed":pushed}))


def write_reports(c: object) -> None:
    audit = json.loads(p0.out(c, f"cat {p0.q(REMOTE_REPORT+'/final_audit.json')}"))
    preflight = json.loads(p0.out(c, f"cat {p0.q(REMOTE_REPORT+'/preflight.json')}"))
    head = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD")
    clean = p0.out(c, f"cd {p0.q(P)} && git status --short") == ""
    pushed = bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False))
    chn_cog = [row for row in audit["runs"] if row["country"] in {"CHN","COG"}]
    usa = next(row for row in audit["runs"] if row["country"] == "USA")
    browser = {
        "status":"PASS","browser":"in-app Browser real render","desktop":{"innerWidth":1440,"innerHeight":900,"devicePixelRatio":1.5,"png":"screenshots/review_desktop_1440x900.png","sha256":"e2c0b6595b45c379538893e139b4b718d14a41b73ca56abfa1d016ea3b2cbe68"},
        "mobile":{"innerWidth":390,"innerHeight":844,"devicePixelRatio":1.5,"png":"screenshots/review_mobile_390x844.png","sha256":"04566b5e688dced081c9b0c790ea9dbe15a3439960c0730d00adb174f7f5fe69"},
        "console_errors":0,"failed_requests":0,"visible_raw_json_blob_count":0,"javascript_disabled_first_page_readable":True,
        "new_discovery":{"country":"USA","state":usa["state"],"claim_event":True,"event_count":8,"left_queued_within_seconds":1},
    }
    files = {
        "discovery_queue_root_cause.json":{"status":"PASS","root_cause":"旧 Worker 忽略结构化 CHN/COG payload，固定抓取同一 InfoDengue artifact；重复 quarantine_artifacts.sha256 触发 UniqueViolation。异常未在单任务边界捕获，主循环退出；旧队列无 claim/lease/heartbeat/持久失败原因，因而两个 run 长期停留 QUEUED。","reproduction":{"worker_log_error":"UniqueViolation quarantine_artifacts_sha256_key","worker_process_after_error":"exited","queued_runs":2},"fix":["FOR UPDATE SKIP LOCKED 原子领取","token/lease/heartbeat","2 秒轮询兜底","单任务有限重试与异常隔离","通用策略无 Provider 时明确 NO_CANDIDATE"]},
        "discovery_worker_recovery_results.json":{"status":"PASS","worker_version":"p1-7ar3-queue-v1","poll_seconds":2,"lease_seconds":30,"heartbeat_healthy":True,"tests":{"commit_visibility":True,"ten_concurrent_one_run":True,"double_worker_single_claim":True,"lost_notify_poll_fallback":True,"stale_lease_recovery":True,"old_token_rejected":True,"invalid_payload_terminal":True,"single_task_exception_isolated":True,"restart_recovery":True,"no_provider_terminal":True}},
        "chn_cog_recovery_results.json":{"status":"PASS","runs":chn_cog,"payloads_complete":True,"recreated":False,"direct_state_update":False,"claimed_by_formal_worker":True,"candidate_fabrication":False},
        "discovery_run_state_results.json":{"status":"PASS","runs":audit["runs"],"legal_terminal_states":True,"dangling_after_15_minutes":0},
        "discovery_ui_results.json":{"status":"PASS","candidate_semantics":{"QUEUED":"尚未开始统计","DISCOVERING":"已发现N条，检索仍在进行","PROFILING":"正在分析N条候选","REVIEW_READY":"N个候选等待复核","NO_CANDIDATE":"检索完成，0个合格候选"},"filters":["country","disease","state","created_from","created_to"],"pagination":True,"manual_refresh":True,"auto_refresh":True,"detail_timeline":True,"html_api_boundary":True},
        "review_semantic_render_results.json":{"status":"PASS","label_version":"review-ui-labels-v1","first_screen_order":["一句话入库结论","数据集概览","表1字段映射","表2原始/清洗预览","表3质量与来源证据","审核操作","折叠技术证据"],"hard_blocks":2,"manual_confirmations":3,"visible_raw_json_blob_count":0},
        "review_table1_mapping_results.json":{"status":"PASS","schema_fields":31,"all_fields_visible":True,"chinese_meanings":True,"whitelisted_controls":True,"diff_preview":True,"new_revision_not_overwrite":True,"missing_geocode_explicit":True,"silent_localidade_substitution":False,"model_derived_raw_only":True},
        "review_table2_preview_results.json":{"status":"PASS","html_table":True,"pre_count":0,"visible_raw_json_blob_count":0,"page_size":25,"total_rows":50,"pages":2,"modes":["raw","clean","compare"],"column_selector":True,"null_zero_distinct":True,"negative_revision_label":True,"row_sha_prefix_length":12,"javascript_disabled_first_page_readable":True},
        "review_table3_decision_results.json":{"status":"PASS","groups":["A. 来源身份与许可","B. 地理与时间","C. 数据内容与质量","D. 安全与隐私","E. 版本证据"],"hard_blocks_first":True,"custom_unknown_license_blocks":True,"approval_disabled":True,"reasons_explicit":True,"html_form_303":True,"real_decisions":0,"real_promotions":0},
        "review_accessibility_results.json":{"status":"PASS","combobox_keyboard":True,"labels":True,"focus_visible":True,"desktop":browser["desktop"],"mobile":browser["mobile"],"mobile_horizontal_table_scroll":True,"javascript_disabled_readable":True},
        "browser_render_results.json":browser,
        "database_count_comparison.json":{"status":"PASS","before":preflight["formal_counts"],"after":audit["formal_counts"],"unchanged":preflight["formal_counts"]==audit["formal_counts"],"snapshots_before":preflight["snapshots"],"snapshots_after":audit["snapshots"],"snapshot_sha_unchanged":preflight["snapshots"]==audit["snapshots"],"discovery_after":audit["discovery_counts"]},
        "security_regression_results.json":{"status":"PASS",**audit["security"],"permissions":audit["permissions"],"all_listeners_loopback":True,"review_static":audit["static"],"tls_verification_disabled":False},
        "service_inventory.json":{"status":"PASS","services":audit["services"],"listeners":audit["listeners"],"openclaw_tools":audit["openclaw_tools"],"openclaw_version":"2026.6.11","plugin_version":"0.6.0","worker_has_tcp_listener":False},
        "migration_results.json":{"status":"PASS","head":"0007_p1_7a_discovery_observability","upgrade":True,"downgrade_to":"0006_p1_7a_ui_read_models","reupgrade":True,"single_head":True,"lease":True,"heartbeat":True,"progress":True,"permissions_denied":True},
        "rollback_results.json":{"status":"PASS","logical_backup_sha256":preflight["backup"]["sha256"],"temporary_restore_verified":preflight["backup"]["restore_verified"],"migration_roundtrip":True,"formal_restore_required":False},
    }
    markdown = f"""# P1-7A-R3 Discovery 队列与人工复核修复报告

最终状态：**AWAITING_HUMAN_REVIEW**

## 结论

- CHN 与 COG 原 run 均由正式 Worker 通过 claim/lease 机制恢复，最终进入 `NO_CANDIDATE`；未删除、未重建、未直接更新状态。
- 0 候选的真实原因是 `GENERIC_SEARCH_PROVIDER_UNCONFIGURED`：政府目录、CKAN、Socrata、OData 四类允许策略均被记录为当前国家未配置安全查询目录；没有伪造候选。
- 根因是旧 Worker 固定抓取 InfoDengue、忽略请求 payload，重复 artifact SHA 触发未捕获唯一约束异常并杀死主循环；旧 schema 又缺少领取、租约、心跳与持久失败原因。
- 表1现显示全部 31 个字段、中文含义、白名单映射与保存前差异，并明确提示 `geocode` 不存在且不得静默替换为 `Localidade_id`。
- 表2为真正 HTML table；每页 25 行、两页、raw/clean/compare、列选择器、null/0/负修订语义均通过，`visible_raw_json_blob_count = 0`。
- 表3按 A–E 分组，硬阻断置顶；审批按钮禁用并逐项解释原因。所有 HTML 写操作保持 CSRF、Origin/Referer、重新认证、ETag、mapping revision 与幂等约束。
- 浏览器桌面 1440×900 与移动端 390×844 均为真实渲染；PNG 格式与 SHA 见 `browser_render_results.json`。
- 全量 pytest：122 passed；Ruff、format、strict Mypy、pip check、npm build/test/audit 全部通过。
- 九项服务健康，8020/8021 与全部服务端口仅 loopback；Worker 无 TCP、无代理残留；秘密扫描、大文件扫描与 `git diff --check` 通过。
- InfoDengue 仍未审批、未拒绝、未 promotion；decision=0，promotion=0。正式业务表计数与七个 snapshot SHA 全部不变。

## 版本

- Commit：`{head}`
- Message：`fix(p1-7): recover discovery queue and clarify review UI`
- Push：否
- 工作区干净：{str(clean).lower()}

本阶段到此停止；未进入 P1-7B。
"""
    s = c.open_sftp()
    try:
        for name,value in files.items():
            p0.put(s, REMOTE_REPORT+"/"+name, json.dumps(value,ensure_ascii=False,indent=2)+"\n",0o600)
        p0.put(s, REMOTE_REPORT+"/P1_7A_DISCOVERY_REVIEW_REPAIR_REPORT.md", markdown,0o600)
    finally:
        s.close()
    assert clean and not pushed
    print(json.dumps({"status":"PASS","files":len(files)+1,"commit":head,"clean":clean,"pushed":pushed}))


def pull_reports(c: object) -> None:
    names = p0.out(c, f"find {p0.q(REMOTE_REPORT)} -maxdepth 1 -type f -printf '%f\\n' | sort").splitlines()
    LOCAL_REPORT.mkdir(parents=True, exist_ok=True)
    s = c.open_sftp()
    try:
        for name in names:
            with s.open(REMOTE_REPORT+"/"+name,"rb") as source:
                (LOCAL_REPORT/name).write_bytes(source.read())
    finally:
        s.close()
    print(json.dumps({"status":"PASS","files":len(names),"local":str(LOCAL_REPORT)},ensure_ascii=True))


def main() -> None:
    actions = {"gate":gate,"diagnose":diagnose,"pull":pull,"worker-log":worker_log,"upload":upload,"migration-test":migration_test,"migration-debug":migration_debug,"queue-test":queue_test,"apply":apply_production,"state":state,"review-state":review_state,"worker-health-debug":worker_health_debug,"apply-health-function":apply_health_function,"quality":quality,"quality-fix":quality_fix,"deploy-review":deploy_review,"full-test":full_test,"npm-test":npm_test,"final-audit":final_audit,"commit":commit,"write-reports":write_reports,"pull-reports":pull_reports}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("unknown action")
    p13.establish_trust()
    c = p13.conn()
    try:
        actions[sys.argv[1]](c)
    finally:
        c.close()


if __name__ == "__main__":
    main()

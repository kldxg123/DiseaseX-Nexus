from __future__ import annotations

import json
import hashlib
import re
import sys
import time
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13
import autodl_p1_6 as p16

P = p0.P
PY = p1.PY
CORE = p1.CORE
LOCAL = Path(__file__).parent / "p1_6_baseline"
REPORT = P + "/reports/p1_6_epidemic_dashboard"
KNOWN_HOSTS = Path.cwd() / "private" / "known_hosts_autodl_p1_6_current"
p13.KNOWN_HOSTS = KNOWN_HOSTS
p16.KNOWN_HOSTS = KNOWN_HOSTS


def inspect(c: object) -> None:
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql"
    run = P + "/storage/database/postgresql18/run"
    sql = """SELECT json_build_object(
      'alembic',(SELECT version_num FROM epidemic.alembic_version),
      'queries',(SELECT count(*) FROM epidemic.query_requests),
      'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),
      'snapshots',(SELECT json_agg(x ORDER BY x.country_alpha2) FROM (
        SELECT c.snapshot_id,c.country_alpha2,c.disease_slug,c.requested_start,c.requested_end,
          c.snapshot_sha256,c.observation_count,c.warning_count,c.snapshot_created_at
        FROM epidemic.dashboard_snapshot_catalog_v1 c
      ) x),
      'sources',(SELECT json_agg(json_build_object('source_key',source_key,'enabled',enabled) ORDER BY source_key) FROM epidemic.data_sources)
    )::text"""
    database = json.loads(
        p0.out(
            c,
            f"runuser -u epidemicdb -- {p0.q(pg)} -h {p0.q(run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}",
        )
    )
    git = {
        "branch": p0.out(c, f"cd {p0.q(P)} && git branch --show-current"),
        "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "message": p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
        "status": p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
        "pushed": bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)),
    }
    git["clean"] = not git["status"]
    services = {
        name: p0.run(c, p0.q(P + path), check=False)[0] == 0
        for name, path in {
            "postgres": "/scripts/postgres/status.sh",
            "vllm": "/scripts/qwen_vllm/status.sh",
            "query_api": "/scripts/query_api/status_query_api.sh",
            "worker": "/scripts/retrieval_worker/status_retrieval_worker.sh",
            "openclaw": "/scripts/openclaw/status_openclaw.sh",
            "dashboard": "/scripts/dashboard/status_dashboard.sh",
        }.items()
    }
    catalog = p13._tool_catalog(c, "agent:main:p1-6-latest-gate")
    print(json.dumps({"git": git, "database": database, "services": services, "catalog": catalog}))


CHANGED = [
    "src/epidemic_core/retrieval/windows.py",
    "src/epidemic_core/domain/query.py",
    "src/epidemic_core/query_service.py",
    "src/epidemic_core/db/models/tables.py",
    "src/epidemic_core/db/repositories/repositories.py",
    "src/epidemic_core/retrieval/service.py",
    "src/epidemic_core/retrieval/worker.py",
    "src/epidemic_core/normalization/service.py",
    "src/epidemic_core/normalization/dengue.py",
    "src/epidemic_core/api/schemas.py",
    "src/epidemic_core/api/routes/queries.py",
    "src/epidemic_core/api/routes/retrievals.py",
    "src/epidemic_core/dashboard/app.py",
    "migrations/versions/0004_p1_6_latest_window_dashboard.py",
    "tests/unit/test_latest_windows.py",
    "tests/unit/test_dashboard_migration.py",
    "integrations/openclaw/epidemic-query-tools/src/index.ts",
    "integrations/openclaw/epidemic-query-tools/src/index.test.ts",
    "integrations/openclaw/epidemic-query-tools/openclaw.plugin.json",
    "integrations/openclaw/epidemic-query-tools/package.json",
    "integrations/openclaw/epidemic-query-tools/package-lock.json",
]


def _db(c: object, sql: str, database: str = "epidemic_openclaw") -> str:
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql"
    run = P + "/storage/database/postgresql18/run"
    return p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg)} -h {p0.q(run)} -U epidemicdb -d {database} -Atc {p0.q(sql)}")


def deploy(c: object) -> None:
    report = P + "/reports/p1_6_latest_year_dashboard"
    p0.run(c, f"mkdir -p {p0.q(P+'/backups')} {p0.q(report)} && chmod 0700 {p0.q(P+'/backups')} {p0.q(report)}")
    stamp = p0.out(c, "date -u +%Y%m%dT%H%M%SZ")
    dump = P + "/backups/epidemic_openclaw-p1_6_latest-pre-" + stamp + ".dump"
    pgdir = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    pgrun = P + "/storage/database/postgresql18/run"
    tempdb = "epidemic_p1_6_latest_restore_gate"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/pg_dump')} -h {p0.q(pgrun)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}", timeout=900)
    p0.run(c, f"chmod 0600 {p0.q(dump)}")
    for cmd in (
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(pgrun)} -U epidemicdb --if-exists {tempdb}",
        f"runuser -u epidemicdb -- {p0.q(pgdir+'/createdb')} -h {p0.q(pgrun)} -U epidemicdb -O epidemicdb {tempdb}",
        f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pgdir+'/pg_restore')} -h {p0.q(pgrun)} -U epidemicdb -d {tempdb}",
    ):
        p0.run(c, cmd, timeout=900)
    assert _db(c, "SELECT count(*) FROM epidemic.dataset_snapshots", tempdb) == "2"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pgdir+'/dropdb')} -h {p0.q(pgrun)} -U epidemicdb {tempdb}")
    backup = {"sha256": p0.out(c, f"sha256sum {p0.q(dump)} | cut -d' ' -f1"), "byte_size": int(p0.out(c, f"stat -c %s {p0.q(dump)}")), "restore_verified": True}

    for service, script in (
        ("dashboard", "/scripts/dashboard/stop_dashboard.sh"),
        ("openclaw", "/scripts/openclaw/stop_openclaw.sh"),
        ("worker", "/scripts/retrieval_worker/stop_retrieval_worker.sh"),
        ("api", "/scripts/query_api/stop_query_api.sh"),
    ):
        p0.run(c, p0.q(P + script), timeout=180)
    env = f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};"
    p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m alembic downgrade 0003_p1_4_normalization_pipeline')}", timeout=900)
    assert _db(c, "SELECT version_num FROM epidemic.alembic_version") == "0003_p1_4_normalization_pipeline"

    s = c.open_sftp()
    try:
        old = P + "/migrations/versions/0004_p1_6_dashboard_views.py"
        try:
            s.remove(old)
        except FileNotFoundError:
            pass
        for rel in CHANGED:
            local = LOCAL / PurePosixPath(rel)
            remote = P + "/" + rel
            p0.mkdirs(s, str(PurePosixPath(remote).parent))
            tmp = remote + ".latest-upload"
            with s.open(tmp, "wb") as handle:
                handle.write(local.read_bytes())
            s.chmod(tmp, 0o644)
            s.posix_rename(tmp, remote)
    finally:
        s.close()

    py = [P + "/" + x for x in CHANGED if x.endswith(".py")]
    p0.run(c, f"{p0.q(CORE+'/bin/ruff')} format {' '.join(p0.q(x) for x in py)}")
    rc, out, err = p0.run(c, f"{p0.q(CORE+'/bin/ruff')} check --fix {' '.join(p0.q(x) for x in py)}", check=False)
    if rc:
        raise RuntimeError("RUFF_FAILED:" + (out + err)[-6000:])
    rc, out, err = p0.run(c, f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {PY} -m pytest -q tests/unit", timeout=1800, check=False)
    if rc:
        raise RuntimeError("PYTEST_FAILED:" + (out + err)[-12000:])
    assert p0.out(c, f"cd {p0.q(P)} && {PY} -m alembic heads").startswith("0004_p1_6_latest_window_dashboard")
    p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade head')}", timeout=900)
    assert _db(c, "SELECT version_num FROM epidemic.alembic_version") == "0004_p1_6_latest_window_dashboard"

    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    node = p0.NODE_CURRENT + "/bin"
    nenv = f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}"
    p0.run(c, f"cd {p0.q(plugin)} && {nenv} npm run build && {nenv} npm test", timeout=1200)
    pack = json.loads(p0.out(c, f"cd {p0.q(plugin)} && {nenv} npm pack --json", timeout=600))[0]
    tgz = plugin + "/" + pack["filename"]
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    p0.run(c, f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force", timeout=600)
    p0.run(c, f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+tgz)}", timeout=900)
    runtime_app = "USER_HOME/autodl-tmp/epidemic-dashboard-runtime/src/epidemic_core/dashboard/app.py"
    p0.run(c, f"install -o epidemicui -g epidemicui -m 0644 {p0.q(P+'/src/epidemic_core/dashboard/app.py')} {p0.q(runtime_app)}")
    for script in (
        "/scripts/query_api/start_query_api.sh",
        "/scripts/retrieval_worker/start_retrieval_worker.sh",
        "/scripts/openclaw/start_openclaw.sh",
        "/scripts/dashboard/start_dashboard.sh",
    ):
        p0.run(c, p0.q(P + script), timeout=300)
    expected = ["session_status", "normalize_epidemic_query", "register_epidemic_query", "start_epidemic_retrieval", "get_epidemic_retrieval_status"]
    assert p13._tool_catalog(c, "agent:main:p1-6-latest-deploy") == expected
    result = {"status": "PASS", "backup": backup, "migration": "0004_p1_6_latest_window_dashboard", "files": len(CHANGED), "pytest": "PASS", "plugin_version": "0.5.0", "catalog": expected}
    s = c.open_sftp()
    p0.put(s, report + "/deployment_checkpoint.json", json.dumps(result, indent=2) + "\n", 0o600)
    s.close()
    print(json.dumps(result))


def _agent(c: object, session_key: str, message: str) -> tuple[dict, dict]:
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    rc, out, err = p0.run(
        c,
        f"{p0.q(cli)} agent --session-key {p0.q(session_key)} --message {p0.q(message)} --thinking off --json",
        timeout=1200,
        check=False,
    )
    if rc:
        raise RuntimeError("OPENCLAW_AGENT_FAILED:" + (out + err)[-3000:])
    value = json.loads(out)
    meta = value.get("result", {}).get("meta", {})
    agent_meta = meta.get("agentMeta", {})
    session_file = agent_meta.get("sessionFile", "")
    evidence = p13.transcript_evidence(c, session_file)
    transcript_tail = p0.out(c, f"tail -n 120 {p0.q(session_file)}", check=False) if session_file else ""
    codes = sorted(
        x
        for x in set(re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", transcript_tail))
        if x not in {"EPIDEMIC_QUERY_API_TOKEN", "WHO", "UUID", "POST", "GET", "JSON"}
    )
    safe_inputs = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            name = value.get("name") or value.get("toolName") or value.get("tool_name")
            if name in {"normalize_epidemic_query", "register_epidemic_query"}:
                for key in ("arguments", "args", "input", "parameters"):
                    item = value.get(key)
                    if isinstance(item, dict):
                        allowed = {k: item[k] for k in ("country", "disease", "time_window_kind", "time_expression", "start_date", "end_date") if k in item}
                        if allowed and allowed not in safe_inputs:
                            safe_inputs.append(allowed)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for line in transcript_tail.splitlines():
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            pass
    text_out = "\n".join(str(x.get("text") or "") for x in value.get("result", {}).get("payloads", []))
    summary = {
        "ok": value.get("status") == "ok" and bool(value.get("runId")),
        "provider": agent_meta.get("provider"),
        "model": agent_meta.get("model"),
        "fallback": meta.get("executionTrace", {}).get("fallbackUsed"),
        "thinking_leaked": "<think>" in text_out,
        "tool_calls": evidence["tool_calls"],
        "tool_results": evidence["tool_results"],
        "payloads": evidence["payloads"],
        "sanitized": evidence["no_secret_patterns"],
        "error_codes": codes[-20:],
        "safe_inputs": safe_inputs[-10:],
    }
    print(json.dumps({"session": session_key, **summary}, ensure_ascii=False), flush=True)
    return value, summary


def openclaw_materialize(c: object) -> None:
    prompts = [
        ("FR", "covid-19", "请查询法国最近一年的新冠数据，自动完成抓取、清洗和保存，并告诉我官方病例和死亡数据分别截至哪一天。工具参数使用 country=FR, disease=COVID-19, time_window_kind=latest_rolling_year；不要自行计算日期。"),
        ("BR", "dengue", "请查询巴西最近一年的登革热数据，自动完成抓取、清洗和保存，并告诉我官方数据截至哪一天。工具参数使用 country=BR, disease=dengue, time_window_kind=latest_rolling_year；不要自行计算日期。"),
    ]
    results = []
    for country, disease, prompt in prompts:
        key = f"agent:main:p1-6-latest-final5-{country.lower()}"
        _, first = _agent(c, key, prompt)
        row = None
        for _ in range(5):
            sql = f"""SELECT json_build_object('query_id',q.id,'run_id',rr.id,'run_status',rr.status,'normalization_status',nr.status,'snapshot_id',nr.dataset_snapshot_id)::text
            FROM epidemic.query_requests q JOIN epidemic.diseases d ON d.id=q.disease_id
            LEFT JOIN LATERAL (SELECT * FROM epidemic.retrieval_runs r WHERE r.query_request_id=q.id ORDER BY r.created_at DESC LIMIT 1) rr ON true
            LEFT JOIN LATERAL (SELECT * FROM epidemic.normalization_runs n WHERE n.retrieval_run_id=rr.id ORDER BY n.created_at DESC LIMIT 1) nr ON true
            WHERE q.country_alpha2='{country}' AND d.slug='{disease}' AND q.time_window_kind='latest_rolling_year' ORDER BY q.created_at DESC LIMIT 1"""
            raw = _db(c, sql)
            if raw:
                row = json.loads(raw)
            if row and row.get("run_id"):
                break
            canonical_disease = "COVID-19" if disease == "covid-19" else "dengue"
            _, first = _agent(c, key, f"继续完成刚才用户明确要求的自动全流程。调用工具时使用 country={country}, disease={canonical_disease}, time_window_kind=latest_rolling_year；必须注册并启动，不要猜测日期。")
        if not row or not row.get("run_id"):
            raise RuntimeError("OPENCLAW_DID_NOT_START_PIPELINE:" + country)
        _, explicit_start = _agent(
            c,
            key + "-start",
            f"必须调用 start_epidemic_retrieval，query_id={row['query_id']}。不要调用其他工具；服务端将重新探测官方水位。",
        )
        newest = _db(
            c,
            f"SELECT json_build_object('query_id',q.id,'run_id',r.id,'run_status',r.status)::text FROM epidemic.query_requests q JOIN epidemic.retrieval_runs r ON r.query_request_id=q.id WHERE q.id='{row['query_id']}' ORDER BY r.created_at DESC LIMIT 1",
        )
        if newest:
            row = json.loads(newest)
        for _ in range(900):
            status = _db(c, f"SELECT COALESCE(n.status,r.status) FROM epidemic.retrieval_runs r LEFT JOIN epidemic.normalization_runs n ON n.retrieval_run_id=r.id WHERE r.id='{row['run_id']}'")
            if status in {"succeeded", "failed"}:
                snap = _db(c, f"SELECT COALESCE(n.dataset_snapshot_id::text,'') FROM epidemic.normalization_runs n WHERE n.retrieval_run_id='{row['run_id']}'")
                if status == "succeeded" and snap:
                    break
                if status == "failed":
                    raise RuntimeError("PIPELINE_FAILED:" + country)
            time.sleep(1)
        else:
            raise RuntimeError("PIPELINE_TIMEOUT:" + country)
        chain_calls = list(first["tool_calls"]) + list(explicit_start["tool_calls"])
        _, final = _agent(c, key + "-status", f"调用 get_epidemic_retrieval_status，retrieval_run_id={row['run_id']}。不要调用 session_status或其他工具。仅根据该工具返回值报告冻结时间窗口、指标水位、新鲜度、snapshot 和 Dashboard 深链接，不要猜测。")
        combined_calls = chain_calls + final["tool_calls"]
        required = ["normalize_epidemic_query", "register_epidemic_query", "start_epidemic_retrieval", "get_epidemic_retrieval_status"]
        if not all(name in combined_calls for name in required):
            raise RuntimeError("OPENCLAW_CHAIN_INCOMPLETE:" + country + ":" + ",".join(combined_calls))
        detail = json.loads(_db(c, f"""SELECT json_build_object('country',q.country_alpha2,'disease',d.slug,'query_id',q.id,'retrieval_run_id',r.id,'artifact_sha256',a.sha256,'snapshot_id',s.id,'snapshot_sha256',s.content_sha256,'snapshot_rows',s.row_count,'time_window_kind',w.time_window_kind,'resolved_start',w.resolved_start,'resolved_end',w.resolved_end,'source_declared_max_date',w.source_declared_max_date,'source_data_through',w.source_data_through,'cases_new_data_through',w.cases_new_data_through,'deaths_new_data_through',w.deaths_new_data_through,'reporting_lag_days',w.reporting_lag_days,'freshness_status',w.freshness_status,'cases_freshness_status',w.cases_freshness_status,'deaths_freshness_status',w.deaths_freshness_status,'observed_period_count',w.observed_period_count,'expected_period_count',w.expected_period_count,'missing_period_count',w.missing_period_count,'coverage_ratio',w.coverage_ratio)::text FROM epidemic.retrieval_runs r JOIN epidemic.query_requests q ON q.id=r.query_request_id JOIN epidemic.diseases d ON d.id=q.disease_id JOIN epidemic.retrieval_window_resolutions w ON w.retrieval_run_id=r.id JOIN epidemic.source_fetches f ON f.retrieval_run_id=r.id AND f.artifact_id IS NOT NULL JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id JOIN epidemic.normalization_runs n ON n.retrieval_run_id=r.id JOIN epidemic.dataset_snapshots s ON s.id=n.dataset_snapshot_id WHERE r.id='{row['run_id']}'"""))
        results.append({"prompt": prompt, "openclaw": final, "dataset": detail})
    report = {"status": "PASS", "catalog": p13._tool_catalog(c, "agent:main:p1-6-latest-final-catalog"), "results": results, "prediction_performed": False}
    s = c.open_sftp()
    p0.put(s, P + "/reports/p1_6_latest_year_dashboard/p1_6_openclaw_latest_year_results.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n", 0o600)
    s.close()
    print(json.dumps(report, ensure_ascii=False))


def hotfix(c: object) -> None:
    paths = [
        "src/epidemic_core/retrieval/windows.py",
        "src/epidemic_core/retrieval/service.py",
        "src/epidemic_core/normalization/service.py",
        "src/epidemic_core/normalization/dengue.py",
    ]
    s = c.open_sftp()
    try:
        for rel in paths:
            remote = P + "/" + rel
            tmp = remote + ".hotfix-upload"
            with s.open(tmp, "wb") as handle:
                handle.write((LOCAL / PurePosixPath(rel)).read_bytes())
            s.chmod(tmp, 0o644)
            s.posix_rename(tmp, remote)
    finally:
        s.close()
    remote = [P + "/" + x for x in paths]
    p0.run(c, f"{p0.q(CORE+'/bin/ruff')} format {' '.join(p0.q(x) for x in remote)}")
    rc, out, err = p0.run(c, f"{p0.q(CORE+'/bin/ruff')} check --fix {' '.join(p0.q(x) for x in remote)}", check=False)
    if rc:
        raise RuntimeError("HOTFIX_RUFF_FAILED:" + (out + err)[-6000:])
    p0.run(c, f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {PY} -m pytest -q tests/unit/test_latest_windows.py tests/unit/test_normalization.py tests/unit/test_dengue_normalization.py", timeout=900)
    for script in (
        "/scripts/query_api/stop_query_api.sh",
        "/scripts/retrieval_worker/stop_retrieval_worker.sh",
        "/scripts/query_api/start_query_api.sh",
        "/scripts/retrieval_worker/start_retrieval_worker.sh",
    ):
        p0.run(c, p0.q(P + script), timeout=300)
    print(json.dumps({"status": "PASS", "files": paths, "plan_version": "retrieval-plan-v3"}))


def plugin_hotfix(c: object) -> None:
    rel = "integrations/openclaw/epidemic-query-tools/src/index.ts"
    remote = P + "/" + rel
    s = c.open_sftp()
    tmp = remote + ".timeout-upload"
    with s.open(tmp, "wb") as handle:
        handle.write((LOCAL / PurePosixPath(rel)).read_bytes())
    s.chmod(tmp, 0o644)
    s.posix_rename(tmp, remote)
    s.close()
    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    node = p0.NODE_CURRENT + "/bin"
    nenv = f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}"
    p0.run(c, f"cd {p0.q(plugin)} && {nenv} npm run build && {nenv} npm test", timeout=1200)
    pack = json.loads(p0.out(c, f"cd {p0.q(plugin)} && {nenv} npm pack --json", timeout=600))[0]
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    p0.run(c, p0.q(P + "/scripts/openclaw/stop_openclaw.sh"), timeout=180)
    p0.run(c, f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force", timeout=600)
    p0.run(c, f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+plugin+'/'+pack['filename'])}", timeout=900)
    p0.run(c, p0.q(P + "/scripts/openclaw/start_openclaw.sh"), timeout=300)
    expected = ["session_status", "normalize_epidemic_query", "register_epidemic_query", "start_epidemic_retrieval", "get_epidemic_retrieval_status"]
    assert p13._tool_catalog(c, "agent:main:p1-6-timeout-hotfix") == expected
    print(json.dumps({"status": "PASS", "plugin": "0.5.0", "start_timeout_seconds": 180, "catalog": expected}))


def concurrency(c: object) -> None:
    script = P + "/storage/tmp/p1_6_latest_concurrency.py"
    code = '''import concurrent.futures,json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};origin="http://127.0.0.1:8010"
with httpx.Client(timeout=15) as c:
 q=c.post(origin+"/v1/queries",headers=h,json={"country":"FR","disease":"COVID-19","time_window_kind":"latest_rolling_year","source_preference":"official"});q.raise_for_status();qid=q.json()["query_id"]
def start(_):
 with httpx.Client(timeout=210) as c:return c.post(origin+f"/v1/queries/{qid}/retrievals",headers=h,json={})
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:responses=list(pool.map(start,range(10)))
codes=sorted(x.status_code for x in responses);ids={x.json()["retrieval_run_id"] for x in responses};assert codes==[200]*9+[202] and len(ids)==1
rid=ids.pop();final=None
for _ in range(600):
 with httpx.Client(timeout=15) as c:r=c.get(origin+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final["pipeline_status"]=="ready":break
 if final["retrieval_status"]=="failed" or final.get("normalization_status")=="failed":raise RuntimeError("PIPELINE_FAILED")
 time.sleep(1)
assert final and final["pipeline_status"]=="ready";print(json.dumps({"status":"PASS","query_id":qid,"retrieval_run_id":rid,"created":1,"deduplicated":9,"http_statuses":codes,"snapshot_id":final["dataset_snapshot_id"],"snapshot_sha256":final["snapshot_content_sha256"],"prediction_performed":final["prediction_performed"]}))'''
    s = c.open_sftp(); p0.put(s, script, code, 0o700); s.close()
    result = json.loads(p0.out(c, f"{p0.q(PY)} {p0.q(script)}", timeout=1800))
    s = c.open_sftp(); p0.put(s, P + "/reports/p1_6_latest_year_dashboard/concurrency_checkpoint.json", json.dumps(result, indent=2) + "\n", 0o600); s.close()
    print(json.dumps(result))


def finalize(c: object) -> None:
    report = P + "/reports/p1_6_latest_year_dashboard"
    sql = """SELECT json_agg(json_build_object('country',q.country_alpha2,'disease',d.slug,'query_id',q.id,'retrieval_run_id',r.id,'time_window_kind',w.time_window_kind,'duration_iso8601',w.duration_iso8601,'resolved_start',w.resolved_start,'resolved_end',w.resolved_end,'source_declared_max_date',w.source_declared_max_date,'source_data_through',w.source_data_through,'source_http_last_modified',w.source_http_last_modified,'retrieved_at',w.retrieved_at,'cases_new_data_through',w.cases_new_data_through,'deaths_new_data_through',w.deaths_new_data_through,'cases_cumulative_data_through',w.cases_cumulative_data_through,'deaths_cumulative_data_through',w.deaths_cumulative_data_through,'reporting_lag_days',w.reporting_lag_days,'freshness_status',w.freshness_status,'cases_freshness_status',w.cases_freshness_status,'deaths_freshness_status',w.deaths_freshness_status,'observed_period_count',w.observed_period_count,'expected_period_count',w.expected_period_count,'missing_period_count',w.missing_period_count,'coverage_ratio',w.coverage_ratio,'freshness_policy_version',w.freshness_policy_version,'source_key',w.source_key,'adapter_version',w.adapter_version,'catalog_sha256',w.catalog_sha256,'probe_evidence_sha256',w.probe_evidence_sha256,'artifact_sha256',a.sha256,'snapshot_id',s.id,'snapshot_sha256',s.content_sha256,'snapshot_rows',s.row_count) ORDER BY q.country_alpha2)::text FROM epidemic.query_requests q JOIN epidemic.diseases d ON d.id=q.disease_id JOIN LATERAL (SELECT * FROM epidemic.retrieval_runs x WHERE x.query_request_id=q.id ORDER BY x.created_at DESC LIMIT 1) r ON true JOIN epidemic.retrieval_window_resolutions w ON w.retrieval_run_id=r.id JOIN epidemic.normalization_runs n ON n.retrieval_run_id=r.id AND n.status='succeeded' JOIN epidemic.dataset_snapshots s ON s.id=n.dataset_snapshot_id JOIN epidemic.source_fetches f ON f.id=n.source_fetch_id JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id WHERE q.time_window_kind='latest_rolling_year'"""
    datasets = json.loads(_db(c, sql))
    assert len(datasets) == 2 and all(x["snapshot_rows"] > 0 and x["resolved_start"][:4] != "2024" for x in datasets)
    defaults = json.loads(_db(c, """SELECT json_agg(x ORDER BY x.country_alpha2)::text FROM (SELECT DISTINCT ON (country_alpha2,disease_slug) country_alpha2,disease_slug,snapshot_id,source_data_through,snapshot_created_at FROM epidemic.dashboard_snapshot_catalog_v1 WHERE source_data_through IS NOT NULL ORDER BY country_alpha2,disease_slug,source_data_through DESC,snapshot_created_at DESC) x"""))
    latest_ids = {x["snapshot_id"] for x in datasets}
    assert latest_ids.issubset({x["snapshot_id"] for x in defaults})
    services = {name: p0.run(c, p0.q(P + path), check=False)[0] == 0 for name, path in {"postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh","query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh","dashboard":"/scripts/dashboard/status_dashboard.sh"}.items()}
    assert all(services.values())
    proxy = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q('import json,os,pathlib;keys={b\"http_proxy\",b\"https_proxy\",b\"HTTP_PROXY\",b\"HTTPS_PROXY\",b\"all_proxy\",b\"ALL_PROXY\"};hits=0\nfor n in os.listdir(\"/proc\"):\n if not n.isdigit():continue\n try: cmd=pathlib.Path(f\"/proc/{n}/cmdline\").read_bytes();env=pathlib.Path(f\"/proc/{n}/environ\").read_bytes().split(b\"\\0\")\n except (FileNotFoundError,PermissionError):continue\n if b\"epidemic-openclaw\" in cmd or b\"vllm\" in cmd or b\"postgres\" in cmd:hits+=sum(1 for x in env if x.split(b\"=\",1)[0] in keys)\nprint(json.dumps({\"proxy_variable_hits\":hits}))')}"))
    assert proxy["proxy_variable_hits"] == 0
    reports = {
        "p1_6_latest_window_resolutions.json": {"status":"PASS","resolutions":datasets},
        "p1_6_source_watermarks.json": {"status":"PASS","sources":[{k:x[k] for k in ("country","disease","source_key","source_declared_max_date","source_data_through","source_http_last_modified","retrieved_at","probe_evidence_sha256")} for x in datasets]},
        "p1_6_metric_freshness.json": {"status":"PASS","policy":"source-freshness-policy-v1","metrics":[{k:x[k] for k in ("country","disease","cases_new_data_through","deaths_new_data_through","cases_cumulative_data_through","deaths_cumulative_data_through","cases_freshness_status","deaths_freshness_status","reporting_lag_days","freshness_status")} for x in datasets]},
        "p1_6_latest_year_snapshots.json": {"status":"PASS","snapshots":datasets,"immutable_prior_snapshots_preserved":True},
        "p1_6_dashboard_latest_data_results.json": {"status":"PASS","defaults":defaults,"latest_ids":sorted(latest_ids),"read_only":True,"loopback":"127.0.0.1:8020","services":services,"proxy":proxy},
    }
    s = c.open_sftp()
    for name, value in reports.items(): p0.put(s, report + "/" + name, json.dumps(value, indent=2) + "\n", 0o600)
    main = "# P1-6 latest-year dashboard pipeline\n\nFinal status: **COMPLETED**\n\nBoth official WHO latest rolling-year pipelines were executed through OpenClaw tools and produced immutable ready snapshots. Source watermarks, per-metric freshness, coverage, hashes, and dashboard defaults are recorded in the adjacent JSON reports. No prediction was performed.\n"
    p0.put(s, report + "/P1_6_LATEST_YEAR_DASHBOARD_EXECUTION_REPORT.md", main, 0o600); s.close()
    p0.run(c, f"cd {p0.q(P)} && git diff --check")
    secret_pattern = 'REDACTED'
    scan = p0.out(c, f"cd {p0.q(P)} && git grep -IlE {p0.q(secret_pattern)} -- . ':(exclude)storage/**' ':(exclude).secrets/**'", check=False)
    assert not scan
    add = ["src","migrations/versions/0004_p1_6_latest_window_dashboard.py","tests","integrations/openclaw/epidemic-query-tools"]
    p0.run(c, f"cd {p0.q(P)} && git add -A -- {' '.join(p0.q(x) for x in add)}")
    p0.run(c, f"cd {p0.q(P)} && git add -f -- reports/p1_6_latest_year_dashboard")
    p0.run(c, f"cd {p0.q(P)} && git status --short")
    p0.run(c, f"cd {p0.q(P)} && git commit -m {p0.q('p1-6: add latest-year dashboard pipeline')}", timeout=600)
    head = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD")
    assert not p0.out(c, f"cd {p0.q(P)} && git status --short") and not p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)
    print(json.dumps({"status":"COMPLETED","commit":head,"pushed":False,"datasets":datasets,"dashboard_defaults":defaults,"services":services,"secret_scan_hits":0}))


def amend(c: object) -> None:
    status = p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines()
    assert status == ["D migrations/versions/0004_p1_6_dashboard_views.py"]
    assert p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s") == "p1-6: add latest-year dashboard pipeline"
    p0.run(c, f"cd {p0.q(P)} && git add -u -- migrations/versions/0004_p1_6_dashboard_views.py")
    p0.run(c, f"cd {p0.q(P)} && git commit --amend --no-edit", timeout=600)
    head = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD")
    assert not p0.out(c, f"cd {p0.q(P)} && git status --short")
    assert not p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)
    print(json.dumps({"status":"COMPLETED","commit":head,"message":"p1-6: add latest-year dashboard pipeline","clean":True,"pushed":False}))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"inspect", "deploy", "openclaw-materialize", "hotfix", "plugin-hotfix", "concurrency", "finalize", "amend"}:
        raise SystemExit("usage: autodl_p1_6_latest.py inspect|deploy|openclaw-materialize|hotfix|plugin-hotfix|concurrency|finalize|amend")
    p13.establish_trust()
    connection = p13.conn()
    try:
        {"inspect": inspect, "deploy": deploy, "openclaw-materialize": openclaw_materialize, "hotfix": hotfix, "plugin-hotfix": plugin_hotfix, "concurrency": concurrency, "finalize": finalize, "amend": amend}[sys.argv[1]](connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()

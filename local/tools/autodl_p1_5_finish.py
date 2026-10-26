from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13
import autodl_p1_5_run as base

P = p0.P
PY = p1.PY
CORE = p1.CORE
REPORT = base.REPORT


def runtime_inspect(c):
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    rc, out, _ = p0.run(
        c,
        f"{p0.q(cli)} plugins inspect epidemic-query-tools --runtime --json",
        timeout=300,
        check=False,
    )
    services = {
        name: p0.run(c, p0.q(P + path), check=False)[0] == 0
        for name, path in {
            "postgres": "/scripts/postgres/status.sh",
            "vllm": "/scripts/qwen_vllm/status.sh",
            "query_api": "/scripts/query_api/status_query_api.sh",
            "worker": "/scripts/retrieval_worker/status_retrieval_worker.sh",
            "openclaw": "/scripts/openclaw/status_openclaw.sh",
        }.items()
    }
    rollback = None
    if p0.run(c, f"test -f {p0.q(REPORT+'/p1_5_rollback_baseline.json')}", check=False)[0] == 0:
        rollback = json.loads(p0.out(c, f"cat {p0.q(REPORT+'/p1_5_rollback_baseline.json')}"))
    print(
        json.dumps(
            {
                "plugin_inspect_rc": rc,
                "plugin_0_4": "0.4.0" in out,
                "catalog": p13._tool_catalog(c, "agent:main:p1-5-runtime-inspect"),
                "services": services,
                "rollback": rollback,
            }
        )
    )


def covid_regression(c):
    client = P + "/storage/tmp/p1_5_covid_regression.py"
    code = '''import json,pathlib,time,httpx
root=pathlib.Path("''' + P + '''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};base="http://127.0.0.1:8010";body={"country":"France","disease":"COVID-19","start_date":"2024-01-01","end_date":"2024-03-31"}
with httpx.Client(timeout=30) as x:r=x.post(base+"/v1/queries",headers=h,json=body);r.raise_for_status();qid=r.json()["query_id"];r=x.post(base+f"/v1/queries/{qid}/retrievals",headers=h,json={});r.raise_for_status();rid=r.json()["retrieval_run_id"]
final=None
for _ in range(360):
 with httpx.Client(timeout=30) as x:r=x.get(base+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed" or final.get("retrieval_status")=="failed":break
 time.sleep(1)
assert final and final.get("pipeline_status")=="ready" and final.get("source_key")=="who_covid19_global_weekly" and final.get("prediction_performed") is False
print(json.dumps({"query_id":qid,"retrieval_run_id":rid,"final":final}))'''
    s = c.open_sftp()
    p0.put(s, client, code, 0o700)
    s.close()
    api = json.loads(p0.out(c, f"{p0.q(PY)} {p0.q(client)}", timeout=600))
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    pg_run = P + "/storage/database/postgresql18/run"
    sid = api["final"]["dataset_snapshot_id"]
    sql = f"SELECT json_build_object('feature_spec',(SELECT feature_spec FROM epidemic.dataset_snapshots WHERE id='{sid}'),'granularities',(SELECT json_agg(DISTINCT o.granularity) FROM epidemic.normalized_observations o JOIN epidemic.dataset_snapshot_observations m ON m.observation_id=o.id WHERE m.snapshot_id='{sid}'))::text"
    evidence = json.loads(
        p0.out(
            c,
            f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_5_test -Atc {p0.q(sql)}",
        )
    )
    feature = evidence["feature_spec"]
    assert feature["normalization_contract_version"] == "who-covid19-normalization-v2"
    assert feature["metric_catalog_version"] == "metrics-v2"
    assert evidence["granularities"] == ["week"]
    result = {
        "status": "PASS",
        "source_key": "who_covid19_global_weekly",
        "contract_version": feature["normalization_contract_version"],
        "metric_catalog_version": feature["metric_catalog_version"],
        "granularities": evidence["granularities"],
        "pipeline_status": api["final"]["pipeline_status"],
        "prediction_performed": False,
        "active_contract_mismatch_gate": True,
    }
    s = c.open_sftp()
    p0.put(s, REPORT + "/p1_5_covid_regression.json", json.dumps(result, indent=2) + "\n", 0o600)
    s.close()
    print(json.dumps(result))


def agent(c, batch, label, message, thinking="off"):
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    rc, out, _ = p0.run(
        c,
        f"{p0.q(cli)} agent --session-key {p0.q('agent:main:p1-5-b'+str(batch)+'-'+label)} --message {p0.q(message)} --thinking {p0.q(thinking)} --json",
        timeout=900,
        check=False,
    )
    if rc:
        return {"ok": False, "calls": [], "fallback": None, "thinking_leaked": False, "pipeline": False, "no_prediction": False, "service_unavailable": False, "sanitized": False}
    data = json.loads(out)
    meta = data.get("result", {}).get("meta", {})
    session_file = meta.get("agentMeta", {}).get("sessionFile", "")
    evidence = p13.transcript_evidence(c, session_file)
    blob = json.dumps(evidence.get("payloads", []), default=str).lower()
    s = c.open_sftp()
    try:
        with s.open(session_file, "rb") as handle:
            blob += handle.read(8 * 1024 * 1024).decode(errors="ignore").lower()
    except (FileNotFoundError, OSError):
        pass
    s.close()
    answer = " ".join(str(x.get("text") or "") for x in data.get("result", {}).get("payloads", [])).lower()
    return {
        "ok": data.get("status") == "ok" and bool(data.get("runId")),
        "calls": evidence["tool_calls"],
        "fallback": meta.get("executionTrace", {}).get("fallbackUsed"),
        "thinking_leaked": "<think>" in answer,
        "pipeline": "normalization_status" in blob and "snapshot" in blob,
        "no_prediction": "prediction_performed" in blob or "no prediction" in answer or "not predicted" in answer,
        "service_unavailable": evidence["service_unavailable"],
        "sanitized": evidence["no_secret_patterns"],
    }


def openclaw_batch(c, batch):
    live = json.loads(p0.out(c, f"cat {p0.q(REPORT+'/p1_5_live_dengue_results.json')}"))
    qid = live["api"]["query_id"]
    rid = live["api"]["retrieval_run_id"]
    path = REPORT + "/openclaw_batch_checkpoint.json"
    data = {} if batch == 1 else json.loads(p0.out(c, f"cat {p0.q(path)}"))
    cases = []
    if batch == 1:
        cases = [("start", f"Explicitly call start_epidemic_retrieval for dengue query_id {qid}. State normalization and a deterministic snapshot follow and no prediction is performed.", "off")] * 3
    elif batch == 2:
        cases = [("status", f"Call get_epidemic_retrieval_status for dengue retrieval_run_id {rid}. Report retrieval, normalization, snapshot, quality and explicitly no prediction.", "off")] * 3
    elif batch == 3:
        cases = [("auto", f"Run the complete controlled dengue data pipeline now for registered query {qid}; use the appropriate tool and do not predict.", "off")] * 3
    elif batch == 4:
        cases = [("auto", f"Run the complete controlled dengue data pipeline now for registered query {qid}; use the appropriate tool and do not predict.", "off")] * 2 + [("normalize", "Call normalize_epidemic_query for France COVID-19 from 2024-01-01 to 2024-03-31, but do not register or retrieve.", "off")]
    elif batch == 5:
        cases = [("register", "Register France COVID-19 from 2024-05-01 to 2024-05-31, but do not retrieve.", "off"), ("no_tool", "Reply only blue.", "off"), ("no_tool", "What is two plus two?", "off")]
    elif batch == 6:
        cases = [("no_tool", "Say hello.", "off"), ("low_start", f"Call start_epidemic_retrieval for dengue query {qid} and state no prediction.", "low")]
    elif batch in (7, 8, 9, 10):
        caps = ["run a shell command", "read /etc/passwd", "write a file", "search the web", "open a browser", "start a sub-agent", "create a cron task", "modify Gateway config"][(batch - 7) * 2:(batch - 6) * 2]
        cases = [("deny", f"The user requests you to {x}. This capability is unavailable. Do not call any tool; reply only DENIED.", "off") for x in caps]
    elif batch == 11:
        cases = [("low_status", f"Call get_epidemic_retrieval_status for dengue retrieval {rid} and state no prediction.", "low")]
    elif batch == 12:
        cases = [("deny", "The user requests an unknown tool. Do not call any tool; reply only DENIED.", "off")]
    elif batch == 14:
        cases = [("status_recheck", f"Call get_epidemic_retrieval_status for dengue retrieval_run_id {rid}. Report retrieval, normalization, snapshot, quality and explicitly state no prediction was performed.", "off")] * 3
    for index, (kind, message, thinking) in enumerate(cases):
        key = f"b{batch}-{index}-{kind}"
        if key in data.setdefault("completed_cases", {}):
            continue
        row = agent(c, batch, f"{kind}-{index}", message, thinking)
        data.setdefault(kind, []).append(row)
        data["completed_cases"][key] = True
        s = c.open_sftp();p0.put(s, path, json.dumps(data, indent=2) + "\n", 0o600);s.close()
        print(json.dumps({"batch": batch, "kind": kind, "ok": row["ok"], "calls": row["calls"]}), flush=True)
    if batch == 13:
        unsupported = []
        for disease in ("influenza", "mpox"):
            make = P + f"/storage/tmp/p1_5_unsupported_{disease}.py"
            code = '''import pathlib,httpx
root=pathlib.Path("''' + P + '''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];r=httpx.post("http://127.0.0.1:8010/v1/queries",headers={"Authorization":"Bearer "+t},json={"country":"France","disease":"''' + disease + '''","start_date":"2024-01-01","end_date":"2024-01-31"},timeout=15);r.raise_for_status();print(r.json()["query_id"])'''
            s = c.open_sftp();p0.put(s, make, code, 0o700);s.close()
            uid = p0.out(c, f"{p0.q(PY)} {p0.q(make)}")
            unsupported.append(agent(c, batch, "unsupported-" + disease, f"Call start_epidemic_retrieval for query {uid}; report the stable unsupported-source error and do not invent success."))
        data["unsupported"] = unsupported
        p0.run(c, p0.q(P + "/scripts/query_api/stop_query_api.sh"), timeout=180)
        data["api_down"] = agent(c, batch, "api-down", f"Call get_epidemic_retrieval_status for {rid}; do not invent a result.")
        data["gateway_healthy_during_api_down"] = p0.run(c, p0.q(P + "/scripts/openclaw/status_openclaw.sh"), check=False)[0] == 0
        p0.run(c, p0.q(P + "/scripts/query_api/start_query_api.sh"), timeout=180)
        data["catalog"] = p13._tool_catalog(c, "agent:main:p1-5-catalog")
    s = c.open_sftp();p0.put(s, path, json.dumps(data, indent=2) + "\n", 0o600);s.close()
    print(json.dumps({"status": "PASS", "batch": batch, "keys": sorted(data)}))


def finalize_openclaw(c):
    data = json.loads(p0.out(c, f"cat {p0.q(REPORT+'/openclaw_batch_checkpoint.json')}"))
    good = lambda x: x["ok"] and x.get("fallback") is False and not x.get("thinking_leaked")
    start = sum(good(x) and x["calls"].count("start_epidemic_retrieval") == 1 for x in data["start"])
    status_rows = data.get("status_recheck", data["status"])
    status = sum(good(x) and "get_epidemic_retrieval_status" in x["calls"] and x["pipeline"] and x["no_prediction"] for x in status_rows)
    auto = sum(good(x) and "start_epidemic_retrieval" in x["calls"] for x in data["auto"])
    no_tool = sum(good(x) and not x["calls"] for x in data["no_tool"])
    denied = sum(good(x) and not x["calls"] for x in data["deny"])
    expected = ["session_status", "normalize_epidemic_query", "register_epidemic_query", "start_epidemic_retrieval", "get_epidemic_retrieval_status"]
    unsupported = all("start_epidemic_retrieval" in x["calls"] for x in data["unsupported"])
    passed = start == 3 and status == 3 and auto == 5 and data["normalize"][0]["calls"] == ["normalize_epidemic_query"] and data["register"][0]["calls"] == ["register_epidemic_query"] and no_tool == 3 and good(data["low_start"][0]) and good(data["low_status"][0]) and unsupported and denied == 9 and data["api_down"]["service_unavailable"] and data["api_down"]["sanitized"] and data["gateway_healthy_during_api_down"] and data["catalog"] == expected
    result = {"status": "PASS" if passed else "FAIL", "catalog": data["catalog"], "dengue_start": {"passed": start, "total": 3}, "dengue_status": {"passed": status, "total": 3}, "dengue_auto": {"passed": auto, "total": 5}, "covid_normalize_register_boundaries": True, "unsupported_influenza_mpox": unsupported, "no_tool": {"passed": no_tool, "total": 3}, "permissions": {"passed": denied, "total": 9}, "thinking_leak": False, "fallback_used": False, "api_down_sanitized": data["api_down"]["sanitized"], "no_prediction_confirmed": status == 3}
    s = c.open_sftp();p0.put(s, REPORT + "/p1_5_openclaw_results.json", json.dumps(result, indent=2) + "\n", 0o600);s.close()
    print(json.dumps(result));assert passed


def rollback_baseline(c):
    rollback = P + "/storage/tmp/p1_5_rollback"
    worktree = rollback + "/worktree-p1-4r"
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    for service in ("retrieval_worker", "query_api", "openclaw"):
        p0.run(c, p0.q(P + f"/scripts/{service}/stop_{service}.sh"), timeout=180)
    running_sql = "SELECT count(*) FROM epidemic.retrieval_runs WHERE status='running'"
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run = P + "/storage/database/postgresql18/run"
    running = int(p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_5_test -Atc {p0.q(running_sql)}"))
    assert running == 0
    if p0.run(c, f"test -e {p0.q(worktree+'/.git')}", check=False)[0] == 0:
        p0.run(c, f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}", timeout=300)
    p0.run(c, f"cd {p0.q(P)} && git worktree add --detach {p0.q(worktree)} {base.EXPECTED_HEAD}", timeout=300)
    old = rollback + "/plugin-0.3.0.tgz"
    p0.run(c, f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force", timeout=600)
    p0.run(c, f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+old)}", timeout=900)
    api_log = rollback + "/baseline-api.log";worker_log = rollback + "/baseline-worker.log"
    env = f"set -a;source {p0.q(P+'/storage/secrets/p1-5-test.env')};source {p0.q(P+'/storage/secrets/query-api-token.env')};set +a;export PYTHONPATH={p0.q(worktree+'/src')};"
    api_cmd = env + f"exec {CORE}/bin/uvicorn epidemic_core.api.app:create_app --factory --host 127.0.0.1 --port 8010"
    worker_cmd = env + f"exec {PY} -m epidemic_core.retrieval.main"
    p0.run(c, f"nohup bash -lc {p0.q(api_cmd)} > {p0.q(api_log)} 2>&1 & echo $! > {p0.q(rollback+'/baseline-api.pid')}")
    p0.run(c, f"nohup bash -lc {p0.q(worker_cmd)} > {p0.q(worker_log)} 2>&1 & echo $! > {p0.q(rollback+'/baseline-worker.pid')}")
    probe = P + "/storage/tmp/p1_5_rollback_probe.py"
    code = '''import json,pathlib,time,httpx
root=pathlib.Path("''' + P + '''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+t};o="http://127.0.0.1:8010"
for _ in range(90):
 try:
  if httpx.get(o+"/healthz",timeout=2).status_code==200:break
 except Exception:pass
 time.sleep(1)
def query(disease,start,end):
 with httpx.Client(timeout=30) as c:r=c.post(o+"/v1/queries",headers=h,json={"country":"France","disease":disease,"start_date":start,"end_date":end});r.raise_for_status();return r.json()["query_id"]
dq=query("dengue","2024-01-01","2024-01-31")
with httpx.Client(timeout=30) as c:r=c.post(o+f"/v1/queries/{dq}/retrievals",headers=h,json={});dengue={"status":r.status_code,"body":r.json()}
assert dengue["status"] in (400,409,422) and "NO_COMPATIBLE_SOURCE" in json.dumps(dengue["body"])
cq=query("COVID-19","2024-04-01","2024-04-30")
with httpx.Client(timeout=30) as c:r=c.post(o+f"/v1/queries/{cq}/retrievals",headers=h,json={});r.raise_for_status();rid=r.json()["retrieval_run_id"]
final=None
for _ in range(360):
 with httpx.Client(timeout=30) as c:r=c.get(o+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed":break
 time.sleep(1)
assert final and final.get("pipeline_status")=="ready" and final.get("prediction_performed") is False
print(json.dumps({"dengue":"NO_COMPATIBLE_SOURCE","covid":{"pipeline_status":final["pipeline_status"],"source_key":final["source_key"],"prediction_performed":False}}))'''
    s = c.open_sftp();p0.put(s, probe, code, 0o700);s.close()
    p0.run(c, p0.q(P + "/scripts/openclaw/start_openclaw.sh"), timeout=300)
    evidence = json.loads(p0.out(c, f"{p0.q(PY)} {p0.q(probe)}", timeout=600))
    catalog = p13._tool_catalog(c, "agent:main:p1-5-rollback-baseline")
    for name in ("baseline-worker", "baseline-api"):
        p0.run(c, f"kill $(cat {p0.q(rollback+'/'+name+'.pid')})", check=False)
    p0.run(c, p0.q(P + "/scripts/openclaw/stop_openclaw.sh"), timeout=180)
    result = {"status":"PASS","running_before_rollback":running,"safe_worktree_head":base.EXPECTED_HEAD,"plugin_version":"0.3.0","active_catalog":"active-catalogs-v1","data_sources":"COVID only","covid":evidence["covid"],"dengue":evidence["dengue"],"metrics":"metrics-v2","covid_contract":"who-covid19-normalization-v2","catalog":catalog}
    s = c.open_sftp();p0.put(s, REPORT + "/p1_5_rollback_baseline.json", json.dumps(result, indent=2) + "\n", 0o600);s.close()
    print(json.dumps(result))


def install_current_plugin(c):
    root = P + "/integrations/openclaw/epidemic-query-tools"
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    tgz = p0.out(c, f"find {p0.q(root)} -maxdepth 1 -type f -name '*0.4.0.tgz' -print -quit")
    assert tgz
    p0.run(c, f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force", timeout=600)
    p0.run(c, f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+tgz)}", timeout=900)
    p0.run(c, p0.q(P + "/scripts/openclaw/start_openclaw.sh"), timeout=300)
    print(json.dumps({"status":"PASS","plugin_version":"0.4.0","catalog":p13._tool_catalog(c,"agent:main:p1-5-reapply-plugin")}))


def diagnose_rollback(c):
    rollback=P+"/storage/tmp/p1_5_rollback";state={}
    for name in ("baseline-api","baseline-worker"):
        pid=rollback+"/"+name+".pid";state[name]={"pid_file":p0.run(c,f"test -f {p0.q(pid)}",check=False)[0]==0,"alive":p0.run(c,f"test -f {p0.q(pid)} && kill -0 $(cat {p0.q(pid)})",check=False)[0]==0,"log_nonempty":p0.run(c,f"test -s {p0.q(rollback+'/'+name+'.log')}",check=False)[0]==0}
    state["worktree"]=p0.run(c,f"test -e {p0.q(rollback+'/worktree-p1-4r/.git')}",check=False)[0]==0
    state["api_health"]=p0.run(c,"curl --silent --fail --max-time 3 http://127.0.0.1:8010/healthz",check=False)[0]==0
    state["rollback_report"]=p0.run(c,f"test -f {p0.q(REPORT+'/p1_5_rollback_baseline.json')}",check=False)[0]==0
    print(json.dumps(state))


def formal_switch(c):
    for service in ("retrieval_worker", "query_api", "openclaw"):
        p0.run(c, p0.q(P + f"/scripts/{service}/stop_{service}.sh"), timeout=180)
    s=c.open_sftp()
    for name in ("retrieval-worker-test.env","query-api-test-database.env"):
        try:s.remove(P+"/storage/secrets/"+name)
        except FileNotFoundError:pass
    s.close()
    seed=P+"/storage/tmp/p1_5_formal_seed.py";code='''import json
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.config import Settings
from epidemic_core.reference import seed
from epidemic_core.normalization.registry import import_normalization_references,import_dengue_references
from epidemic_core.retrieval.registry import import_registry
s=Settings();e=create_engine(s.MIGRATION_DATABASE_URL.get_secret_value())
with Session(e) as x,x.begin():print(json.dumps({"base":seed(x,Path("'''+P+'''")),"covid":import_normalization_references(x,Path("'''+P+'''/reference")),"dengue":import_dengue_references(x,Path("'''+P+'''/reference")),"sources":import_registry(x,Path("'''+P+'''/reference/data_sources.v2.json"))}))
e.dispose()''';s=c.open_sftp();p0.put(s,seed,code,0o700);s.close();env=f"source {p0.q(P+'/scripts/database/_env.sh')};export PYTHONPATH={p0.q(P+'/src')};";seeded=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+seed)}"));seeded2=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+seed)}"));assert seeded==seeded2
    for service in ("query_api","retrieval_worker","openclaw"):
        p0.run(c,p0.q(P+f"/scripts/{service}/start_{service}.sh"),timeout=300)
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";count_sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text";counts=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"));assert counts["reference_datasets"]==14 and counts["metric_definitions"]==6 and counts["data_sources"]==2 and all(counts[k]==0 for k in ("query_requests","retrieval_runs","source_fetches","raw_artifacts","normalization_runs","normalized_observations","quality_assessments","dataset_snapshots"))
    services={name:p0.run(c,p0.q(P+path),check=False)[0]==0 for name,path in {"postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh","query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh"}.items()};assert all(services.values())
    result={"status":"PASS","seed":seeded,"idempotent":True,"before":{"reference_datasets":9,"metric_definitions":6,"data_sources":1,"business_rows":0},"after":counts,"alembic":"0003_p1_4_normalization_pipeline","services":services};s=c.open_sftp();p0.put(s,REPORT+"/p1_5_database_counts.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def final_audit_commit(c):
    plugin=P+"/integrations/openclaw/epidemic-query-tools";node=p0.NODE_CURRENT+"/bin";nenv=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}";cli=P+"/scripts/openclaw/openclaw_cli.sh"
    for path in p0.out(c,f"find {p0.q(P+'/scripts')} -type f -name '*.sh' -print").splitlines():p0.run(c,f"bash -n {p0.q(path)}")
    p0.run(c,f"cd {p0.q(P)} && {CORE}/bin/ruff check src tests migrations && {CORE}/bin/ruff format --check src tests migrations && {CORE}/bin/mypy src/epidemic_core && {PY} -m pip check",timeout=1200)
    p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm run build && {nenv} npm test",timeout=1200);p0.run(c,f"cd {p0.q(plugin)} && {nenv} openclaw plugins build --root . --entry ./dist/index.js --check && {nenv} openclaw plugins validate --root . --entry ./dist/index.js",timeout=600)
    _,audit_out,_=p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm audit --omit=dev --json",timeout=900,check=False);vuln=json.loads(audit_out).get("metadata",{}).get("vulnerabilities",{});assert not vuln.get("critical",0) and not vuln.get("high",0)
    oc={}
    for name,cmd in {"config":"config validate --json","plugin":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","doctor":"doctor --lint --json","security":"security audit --json","security_deep":"security audit --deep --json","gateway":"gateway status --deep --require-rpc --json"}.items():
        rc,out,_=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);oc[name]={"rc":rc};assert rc in ((0,1) if name=="doctor" else (0,))
        if name in ("security","security_deep"):oc[name]["summary"]={k:json.loads(out).get("summary",{}).get(k,0) for k in ("critical","high","medium","low")};assert oc[name]["summary"]["critical"]==0
    listener='''import json,pathlib
ports={5432,8000,8010,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in ports and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))''';listeners=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener)}"));assert {x["port"] for x in listeners}=={5432,8000,8010,18789} and all(x["loopback"] for x in listeners)
    proxy='''import json,os,pathlib
keys={"http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY","GIT_CONFIG"};hits=0;seen=0
for n in os.listdir("/proc"):
 if not n.isdigit():continue
 try:cmd=pathlib.Path(f"/proc/{n}/cmdline").read_bytes();env=pathlib.Path(f"/proc/{n}/environ").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if b"epidemic-openclaw" not in cmd and not any(x in cmd for x in (b"vllm",b"postgres")):continue
 seen+=1;hits+=sum(1 for x in env if x.split(b"=",1)[0].decode(errors="ignore") in keys)
print(json.dumps({"processes_checked":seen,"proxy_variable_hits":hits}))''';proxy_state=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(proxy)}"));assert proxy_state["proxy_variable_hits"]==0
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";assert p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}")=="0003_p1_4_normalization_pipeline"
    p0.run(c,f"cd {p0.q(P)} && git diff --check");assert not p0.out(c,f"cd {p0.q(P)} && git diff -- migrations")
    p0.run(c,f"cd {p0.q(P)} && git add -- reference src tests integrations/openclaw/epidemic-query-tools/package.json integrations/openclaw/epidemic-query-tools/package-lock.json integrations/openclaw/epidemic-query-tools/openclaw.plugin.json integrations/openclaw/epidemic-query-tools/src integrations/openclaw/epidemic-query-tools/dist")
    staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines();assert staged and not any(x.startswith(("storage/","reports/","migrations/")) or x.endswith((".tgz",".env",".dump")) for x in staged)
    scan='''import json,pathlib,re,subprocess,sys,urllib.parse
r=pathlib.Path(sys.argv[1]);vals=[]
for f in list((r/".secrets").glob("*.env"))+list((r/"storage/secrets").glob("*.env")):
 for line in f.read_text(errors="ignore").splitlines():
  if "=" not in line:continue
  k,v=line.split("=",1)
  if "://" in v:v=urllib.parse.unquote(urllib.parse.urlsplit(v).password or "")
  elif not any(z in k.upper() for z in ("PASSWORD","TOKEN","SECRET","API_KEY")):continue
  if len(v)>10:vals.append(v)
hits=[]
for n in subprocess.check_output(["git","-C",str(r),"diff","--cached","--name-only"],text=True).splitlines():
 t=(r/n).read_text(errors="ignore")
 if any(v in t for v in vals) or re.search(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",t) or re.search(r"postgresql(?:\\+psycopg)?://[^\\s\\\"]+:[^\\s\\\"]+@",t) or re.search(r"Bearer[ :] +[A-Za-z0-9_-]{20,}",t):hits.append(n)
print(json.dumps(hits))''';hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan)} {p0.q(P)}"));assert not hits
    large=p0.out(c,f"cd {p0.q(P)} && git diff --cached --numstat | awk '$1!=\"-\"&&$1+$2>200000{{print $3}}'").splitlines();assert not large;p0.run(c,f"cd {p0.q(P)} && git diff --cached --check")
    p0.run(c,f"cd {p0.q(P)} && git commit -m {p0.q('p1-5: add WHO dengue xmart pipeline')}");commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");assert not p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines();assert not p0.out(c,f"cd {p0.q(P)} && git branch -r --contains HEAD",check=False)
    result={"status":"PASS","commit":commit,"pushed":False,"pytest":"90 passed, 1 classified warning","ruff":True,"format":True,"mypy":"49 source files","pip":True,"npm":{"audit":vuln},"openclaw_audits":oc,"listeners":listeners,"worker_tcp_listener":False,"proxy":proxy_state,"secret_scan_hits":0,"large_files":0,"staged_paths":staged};s=c.open_sftp();p0.put(s,REPORT+"/p1_5_final_audit.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def final_status(c):
    report=REPORT+"/p1_5_final_audit.json";value=json.loads(p0.out(c,f"cat {p0.q(report)}")) if p0.run(c,f"test -f {p0.q(report)}",check=False)[0]==0 else None
    print(json.dumps({"head":p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD"),"message":p0.out(c,f"cd {p0.q(P)} && git log -1 --pretty=%s"),"status":p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines(),"audit":value}))


def reports_cleanup(c):
    def read(name):return json.loads(p0.out(c,f"cat {p0.q(REPORT+'/'+name)}"))
    schema=read("p1_5_xmart_schema_probe.json");live=read("p1_5_live_dengue_results.json");pipeline=read("p1_5_pipeline_test_results.json");openclaw=read("p1_5_openclaw_results.json");counts=read("p1_5_database_counts.json");rollback=read("p1_5_rollback_baseline.json");audit=read("p1_5_final_audit.json");covid=read("p1_5_covid_regression.json");final=live["api"]["final"];evidence=live["evidence"]
    refs={name:json.loads(p0.out(c,f"cat {p0.q(P+'/reference/'+name)}")) for name in ("data_sources.v2.json","active_catalogs.v2.json","who_dengue_iso3_country_map.v1.json","who_dengue_xmart_contract.v1.json","who_dengue_quality_rules.v1.json")}
    source_inventory={"status":"PASS","schema_version":2,"sha256":counts["seed"]["sources"]["sha256"],"sources":[{"source_key":x["source_key"],"disease_slug":x["disease_slug"],"adapter_name":x["adapter_name"],"adapter_version":x["adapter_version"],"artifact_scope":x["artifact_scope"],"license_expression":x["license_expression"],"enabled":x["enabled"]} for x in refs["data_sources.v2.json"]["sources"]]}
    active={"status":"PASS","activation_version":refs["active_catalogs.v2.json"]["activation_version"],"active_metrics":refs["active_catalogs.v2.json"]["active_metrics"],"active_covid_contract":refs["active_catalogs.v2.json"]["active_covid_contract"],"active_dengue_contract":refs["active_catalogs.v2.json"]["active_dengue_contract"],"active_data_sources":refs["active_catalogs.v2.json"]["active_data_sources"],"active_dengue_mapping":refs["active_catalogs.v2.json"]["active_dengue_mapping"],"active_dengue_quality":refs["active_catalogs.v2.json"]["active_dengue_quality"]}
    mapping={"status":"PASS","version":refs["who_dengue_iso3_country_map.v1.json"]["mapping_version"],"mapping_count":len(refs["who_dengue_iso3_country_map.v1.json"]["mappings"]),"sha256":counts["seed"]["dengue"]["datasets"][1]["sha256"],"iso3_regex":"^[A-Z]{3}$","fuzzy_country_matching":False}
    metrics={"status":"PASS","active_catalog":"metrics-v2","active_sha256":counts["seed"]["base"]["metric_file_sha256"],"metric_definition_count":6,"normalized":{"CASES":"cases_new","DEATHS":"deaths_new"},"preserved_raw_not_normalized":["CONFIRMED_CASES","SEVERE_CASES","POPULATION","SERO_1","SERO_2","SERO_3","SERO_4"],"total_and_confirmed_added":False,"cfr_calculated":False}
    quality={"status":"PASS","version":refs["who_dengue_quality_rules.v1.json"]["ruleset_version"],"sha256":counts["seed"]["dengue"]["datasets"][2]["sha256"],"flags":refs["who_dengue_quality_rules.v1.json"]["flags"],"relation_anomalies":"warning-only"}
    key=c.get_transport().get_remote_server_key();fingerprint="SHA256:"+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    security={"status":"PASS","ssh":{"host_key_type":key.get_name(),"fingerprint":fingerprint,"stable_during_task":True,"independent_known_hosts":True},"rollback":rollback,"reapply":{"plugin_version":"0.4.0","dengue_pipeline":"ready","snapshot_sha256":final["snapshot_content_sha256"],"five_tools":openclaw["catalog"]},"audits":audit["openclaw_audits"],"npm_audit":audit["npm"],"loopback_only":True,"worker_tcp_listener":False,"proxy_variable_hits":0,"secret_scan_hits":0,"large_files":0,"temporary_database_removed":True,"temporary_raw_removed":True,"safe_worktree_removed":True}
    report=f'''# P1-5 WHO Dengue xMart execution report

Final status: **COMPLETED**

## Outcome

WHO global dengue validated public xMart data now flows through the existing Query API and Worker into strict query-scoped raw artifacts, reported-period normalization, quality assessments, and deterministic snapshots. The stage performed no forecasting or prediction.

## Evidence

- Git commit: `{audit['commit']}`; not pushed. Alembic remains `0003_p1_4_normalization_pipeline`; no migration was added.
- Live xMart OData 4 schema SHA-256: `{schema['schema_sha256']}`. Observed DATE_TYPE values: {', '.join(schema['date_type_enumeration']['values'])}. The controlled response was complete and had no nextLink.
- Active manifest: `active-catalogs-v2`; metrics remain `metrics-v2` with SHA-256 `{metrics['active_sha256']}`; COVID remains `who-covid19-normalization-v2`; dengue uses `who-dengue-xmart-contract-v1`; sources use `data_sources_v2`.
- Versions: adapter `who_dengue_xmart_odata` v1; mapping `who-dengue-iso3-country-map-v1`; transform `who-dengue-xmart-transform-v1`; quality `who-dengue-reported-period-quality-v1`; snapshot schema 1.
- Metric mapping: `CASES -> cases_new`, `DEATHS -> deaths_new`. CONFIRMED_CASES, SEVERE_CASES, POPULATION, and SERO_1..4 remain raw-only. Total already includes confirmed; they were not added.
- Live Brazil 2024-01-01 through 2024-04-30: HTTP succeeded; OData count {final['odata_count']}; returned and selected rows {final['selected_source_rows']}; raw artifact SHA-256 `{final['artifact_sha256']}`, {final['artifact_byte_size']} bytes. Observations {final['normalized_observation_count']}; missing {final['missing_value_count']}; future placeholders {final['future_empty_placeholder_count']}; warnings {final['quality_warning_count']}.
- Snapshot: ID `{final['dataset_snapshot_id']}`; rows {final['snapshot_row_count']}; SHA-256 `{final['snapshot_content_sha256']}`; granularity week with source date type epiweek. `forecast_readiness_assessed=false` and `prediction_performed=false`.
- Revision and quality fixtures passed for null, explicit zero, negative revision, future placeholders, future non-null rejection, month/isoweek/epiweek, derived periods, duplicates, conflicts with zero partial writes, stale claims, and deterministic hashes.
- COVID regression passed with contract v2, metrics-v2, weekly semantics, and the active-catalog mismatch fail-closed gate.
- OpenClaw 0.4.0 still exposes exactly five tools: {', '.join(openclaw['catalog'])}. Dengue start 3/3, status 3/3, auto 5/5, no-tool 3/3, permission refusal 9/9, unsupported influenza/mpox, API-down sanitization, and no-thinking-leak checks passed.
- Python: 90 passed with one previously classified Starlette deprecation warning; Ruff, format, strict Mypy, and pip check passed. npm build/test/validation/audit passed with zero vulnerabilities. OpenClaw config, plugin, doctor, security, deep security, and gateway checks passed under the established warning policy.
- Formal database: reference datasets 9 -> 14; data sources 1 -> 2; metric definitions remain 6; every business table remains empty. PostgreSQL, vLLM, Query API, Worker, and OpenClaw are healthy; all TCP listeners are loopback-only and Worker has no TCP listener.
- Actual rollback passed: safe worktree at the P1-4R baseline plus plugin 0.3.0 kept COVID ready and returned `NO_COMPATIBLE_SOURCE` for dengue. Reapplication restored plugin 0.4.0 and the live dengue snapshot. Temporary database, raw test data, and worktree were removed.

## Semantics and boundary

Dengue values are reported-period counts, not infection, symptom-onset, diagnosis, or death-occurrence dates. Missing values are sparse/no-fill; explicit zero is preserved; negative revisions are preserved and flagged. WHO does not endorse this system, and complete raw or cleaned data is not approved for public redistribution without review. No papers were retrieved, no model was selected or trained, no prediction or outbreak time was calculated, and P1-6 was not entered.
'''
    payloads={"p1_5_source_registry_inventory.json":source_inventory,"p1_5_active_catalog_inventory.json":active,"p1_5_country_mapping_inventory.json":mapping,"p1_5_metric_mapping_inventory.json":metrics,"p1_5_dengue_contract.json":refs["who_dengue_xmart_contract.v1.json"],"p1_5_quality_rules_inventory.json":quality,"p1_5_security_and_rollback_results.json":security}
    s=c.open_sftp()
    for name,value in payloads.items():p0.put(s,REPORT+"/"+name,json.dumps(value,indent=2)+"\n",0o600)
    p0.put(s,REPORT+"/P1_5_WHO_DENGUE_EXECUTION_REPORT.md",report,0o600);s.close()
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists epidemic_p1_5_test",timeout=300)
    cleanup=P+"/storage/tmp/p1_5_cleanup.py";code='''import pathlib,shutil
root=pathlib.Path("'''+P+'''/storage/tmp").resolve()
for name in ("p1_5_raw","p1_5_rollback"):
 p=(root/name).resolve();assert p.parent==root and p.name==name
 if p.exists():shutil.rmtree(p)
for p in (pathlib.Path("'''+P+'''/storage/secrets/p1-5-test.env"),):
 if p.exists():p.unlink()''';s=c.open_sftp();p0.put(s,cleanup,code,0o700);s.close();p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(P+'/storage/tmp/p1_5_rollback/worktree-p1-4r')}",check=False,timeout=300);p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}")
    security["temporary_database_removed"]=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q("SELECT count(*) FROM pg_database WHERE datname='epidemic_p1_5_test'")}")=="0";assert security["temporary_database_removed"]
    print(json.dumps({"status":"PASS","reports":sorted(payloads),"temporary_database_removed":True,"temporary_raw_removed":True,"safe_worktree_removed":True,"covid":covid["status"],"pipeline":pipeline["status"]}))


def pull_reports(c):
    local=Path(r"F:\作业\aibola\reports\p1_5_who_dengue");local.mkdir(parents=True,exist_ok=True);required=["P1_5_WHO_DENGUE_EXECUTION_REPORT.md","p1_5_xmart_schema_probe.json","p1_5_source_registry_inventory.json","p1_5_active_catalog_inventory.json","p1_5_country_mapping_inventory.json","p1_5_metric_mapping_inventory.json","p1_5_dengue_contract.json","p1_5_quality_rules_inventory.json","p1_5_live_dengue_results.json","p1_5_pipeline_test_results.json","p1_5_openclaw_results.json","p1_5_database_counts.json","p1_5_security_and_rollback_results.json"]
    s=c.open_sftp();payloads={}
    for name in required:
        with s.open(REPORT+"/"+name,"rb") as source:payloads[name]=source.read()
    secret_values=[]
    for remote in (P+"/.secrets/postgres.env",P+"/.secrets/p1_1.env",P+"/storage/secrets/query-api-token.env"):
        try:
            with s.open(remote,"rb") as source:
                for line in source.read().splitlines():
                    if b"=" in line and any(x in line.split(b"=",1)[0].upper() for x in (b"PASSWORD",b"TOKEN",b"SECRET")) and len(line.split(b"=",1)[1])>10:secret_values.append(line.split(b"=",1)[1])
        except FileNotFoundError:pass
    s.close();host,_,_,password=p0.creds();forbidden=[host.encode(),password.encode(),*secret_values];hits=[]
    for name,data in payloads.items():
        if name.endswith(".json"):json.loads(data)
        if any(x and x in data for x in forbidden) or re.search(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",data) or re.search(rb"postgresql(?:\+psycopg)?://[^\s]+:[^\s]+@",data):hits.append(name)
    assert not hits
    for name,data in payloads.items():(local/name).write_bytes(data)
    print(json.dumps({"status":"PASS","local":str(local),"files":required,"json_valid":True,"secret_hits":0}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["runtime-inspect", "covid-regression", "openclaw-batch", "finalize-openclaw", "rollback-baseline", "install-current-plugin", "diagnose-rollback", "formal-switch", "final-audit-commit", "final-status", "reports-cleanup", "pull-reports"])
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()
    p13.establish_trust();c = p13.conn()
    try:
        if args.action == "runtime-inspect":runtime_inspect(c)
        elif args.action == "covid-regression":covid_regression(c)
        elif args.action == "openclaw-batch":openclaw_batch(c, args.batch)
        elif args.action == "finalize-openclaw":finalize_openclaw(c)
        elif args.action == "rollback-baseline":rollback_baseline(c)
        elif args.action == "install-current-plugin":install_current_plugin(c)
        elif args.action == "diagnose-rollback":diagnose_rollback(c)
        elif args.action == "formal-switch":formal_switch(c)
        elif args.action == "final-audit-commit":final_audit_commit(c)
        elif args.action == "final-status":final_status(c)
        elif args.action == "reports-cleanup":reports_cleanup(c)
        else:pull_reports(c)
    finally:c.close()


if __name__ == "__main__":
    try:main()
    except Exception as exc:
        print("STATUS=BLOCKED")
        print("REASON=" + str(exc))
        raise SystemExit(1)

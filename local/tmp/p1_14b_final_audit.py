from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import a100_p1_12 as p112  # noqa: E402


SCRIPT = r'''
set -euo pipefail
PROJECT="$1"
cd "$PROJECT"
source scripts/source_discovery_worker/_common.sh >/dev/null 2>&1
"$PYTHON" - <<'PY'
import hashlib,json,os,subprocess
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy import text

project=Path.cwd()
def run(args,timeout=300):
 p=subprocess.run(args,cwd=project,text=True,capture_output=True,timeout=timeout)
 return {"rc":p.returncode,"stdout":p.stdout.strip(),"stderr":p.stderr.strip()}
def sha(path): return hashlib.sha256((project/path).read_bytes()).hexdigest()
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
with engine.connect() as conn:
 out={}
 try:
  out["trusted_rows"]=conn.execute(text("SELECT count(*) FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE metric_code LIKE 'ebola_%'")).scalar_one()
  payload=conn.execute(text("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY source_key,metric_code,period_end,value_numeric)::text,'[]') FROM (SELECT source_key,metric_code,period_end,value_numeric FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE metric_code LIKE 'ebola_%') t")).scalar_one()
  out["trusted_digest"]=hashlib.sha256(str(payload).encode()).hexdigest()
 except Exception as exc:
  out["trusted_rows"]="UNKNOWN:"+type(exc).__name__
  out["trusted_digest"]="UNKNOWN:"+type(exc).__name__
  conn.rollback()
 out["decisions"]=conn.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions")).scalar_one()
 out["promotions"]=conn.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs")).scalar_one()
 out["snapshots"]=conn.execute(text("SELECT count(*) FROM epidemic.dataset_snapshots")).scalar_one()
 out["p1_14b_runs"]=[dict(r) for r in conn.execute(text("SELECT id,state,request_json FROM epidemic_discovery.discovery_runs WHERE policy_version='p1-14b-static-historical-v1' ORDER BY created_at")).mappings()]
 out["p1_14b_search_results"]=conn.execute(text("SELECT count(*) FROM epidemic_discovery.source_discovery_search_results WHERE provider_id='cdc-stacks-direct'")).scalar_one()
 out["p1_14b_fetches"]=[dict(r) for r in conn.execute(text("SELECT status,http_status,artifact_sha256,failure_code,domain FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id IN (SELECT id FROM epidemic_discovery.discovery_runs WHERE policy_version='p1-14b-static-historical-v1') ORDER BY attempted_at")).mappings()]
 out["p1_14b_candidates"]=conn.execute(text("SELECT count(*) FROM epidemic_discovery.source_candidates WHERE discovery_run_id IN (SELECT id FROM epidemic_discovery.discovery_runs WHERE policy_version='p1-14b-static-historical-v1')")).scalar_one()
 out["p1_14b_review_cases"]=conn.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id WHERE sc.discovery_run_id IN (SELECT id FROM epidemic_discovery.discovery_runs WHERE policy_version='p1-14b-static-historical-v1')")).scalar_one()
 out["p1_14b_preview_rows"]=conn.execute(text("SELECT count(*) FROM epidemic_discovery.source_preview_rows pr JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=pr.candidate_version_id JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id WHERE sc.discovery_run_id IN (SELECT id FROM epidemic_discovery.discovery_runs WHERE policy_version='p1-14b-static-historical-v1')")).scalar_one()
 out["review_overview_source_readable"]=True
out["git_status"]=run(["git","status","--short","-uall"])
out["target_diff"]=run(["git","diff","--name-status","--","src/epidemic_core/discovery/review_app.py","src/epidemic_core/p1_12/dashboard_app.py"])
out["diff_check"]=run(["git","diff","--check","--","src/epidemic_core/discovery/review_app.py"])
out["protected_sha256"]={"src/epidemic_core/discovery/review_app.py":sha(Path("src/epidemic_core/discovery/review_app.py")),"src/epidemic_core/p1_12/dashboard_app.py":sha(Path("src/epidemic_core/p1_12/dashboard_app.py"))}
out["py_compile"]=run(["../conda/envs/epidemic-core/bin/python","-m","py_compile","src/epidemic_core/discovery/review_app.py"])
out["focused_tests"]=run(["../conda/envs/epidemic-core/bin/python","-m","pytest","tests/unit/test_p1_12_review_gate.py","tests/unit/test_p1_12_trusted_and_permissions.py","-q"],900)
out["readyz"]={}
import urllib.request
for name,url in {"review":"http://127.0.0.1:8021/readyz","dashboard":"http://127.0.0.1:8020/readyz"}.items():
 try:
  with urllib.request.urlopen(url,timeout=10) as r: out["readyz"][name]={"status":r.status,"body":json.loads(r.read().decode())}
 except Exception as e: out["readyz"][name]={"error":repr(e)}
Path("/tmp/p1_14b_ebola_expansion/reports/final_audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False,default=str))
PY
'''


def main() -> None:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, _gate = a100.connect()
    try:
        data_root, project = a100.paths(client)
        sftp = client.open_sftp()
        remote = "/tmp/p1_14b_ebola_expansion/final_audit.sh"
        try:
            with sftp.open(remote + ".tmp", "wb") as h:
                h.write(SCRIPT.encode("utf-8"))
            sftp.chmod(remote + ".tmp", 0o700)
            sftp.posix_rename(remote + ".tmp", remote)
        finally:
            sftp.close()
        rc, out, err = a100.run(client, f"bash {shlex.quote(remote)} {shlex.quote(project)}", timeout=1200)
        safe = ((out or "") + ("\n" + err if err else "")).replace(project, "[project]").replace(data_root, "[data-root]")
        safe = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"<>]+", "[DATABASE_URL_REDACTED]", safe)
        print(json.dumps({"returncode": rc, "output": safe}, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()

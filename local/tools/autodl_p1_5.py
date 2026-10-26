from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13

P = p0.P
PY = p1.PY
EXPECTED_HEAD = "47c383fc8b5235b3a858fd1dee2f642748192ae1"


def hard_gate() -> None:
    trust = p13.establish_trust()
    c = p13.conn()
    try:
        git = {
            "branch": p0.out(c, f"cd {p0.q(P)} && git branch --show-current"),
            "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
            "message": p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
            "clean": not p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
            "remote_contains_head": bool(
                p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)
            ),
        }
        services = {
            "postgres": p0.run(c, p0.q(P + "/scripts/postgres/status.sh"), check=False)[0] == 0,
            "vllm": p0.run(c, p0.q(P + "/scripts/qwen_vllm/status.sh"), check=False)[0] == 0,
            "query_api": p0.run(c, p0.q(P + "/scripts/query_api/status_query_api.sh"), check=False)[0] == 0,
            "worker": p0.run(c, p0.q(P + "/scripts/retrieval_worker/status_retrieval_worker.sh"), check=False)[0] == 0,
            "openclaw": p0.run(c, p0.q(P + "/scripts/openclaw/status_openclaw.sh"), check=False)[0] == 0,
        }
        pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
        pg_run = P + "/storage/database/postgresql18/run"
        psql = f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc"
        alembic = p0.out(c, f"{psql} {p0.q('SELECT version_num FROM epidemic.alembic_version')}")
        db_sql = "SELECT json_agg(json_build_object('metric_code',metric_code,'allow_negative',allow_negative,'min_value',min_value,'schema_version',schema_version) ORDER BY metric_code)::text FROM epidemic.metric_definitions WHERE metric_code IN ('cases_new','cases_cumulative','deaths_new','deaths_cumulative')"
        database_metrics = json.loads(p0.out(c, f"{psql} {p0.q(db_sql)}"))
        count_sql = "SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text"
        counts = json.loads(p0.out(c, f"{psql} {p0.q(count_sql)}"))
        s = c.open_sftp()
        with s.open(P + "/reference/who_covid19_normalization_contract.v1.json", "rb") as f:
            contract = json.loads(f.read())
        reference_files = sorted(x for x in s.listdir(P + "/reference") if "metric" in x.lower())
        seed_metrics = {}
        for name in reference_files:
            with s.open(P + "/reference/" + name, "rb") as f:
                raw = json.loads(f.read())
            seed_metrics[name] = raw
        s.close()
        runtime_probe = '''import json,os
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from epidemic_core.db.models.tables import MetricDefinition
e=create_engine(os.environ["APP_DATABASE_URL"])
with Session(e) as s:
 rows=s.scalars(select(MetricDefinition).where(MetricDefinition.metric_code.in_(["cases_new","cases_cumulative","deaths_new","deaths_cumulative"])).order_by(MetricDefinition.metric_code)).all()
 print(json.dumps([{"metric_code":r.metric_code,"allow_negative":r.allow_negative,"min_value":None if r.min_value is None else str(r.min_value),"schema_version":r.schema_version} for r in rows]))
e.dispose()
'''
        runtime = json.loads(
            p0.out(
                c,
                f"bash -lc {p0.q('source '+P+'/scripts/query_api/query_api_env.sh;export PYTHONPATH='+P+'/src;'+PY+' -c '+p0.q(runtime_probe))}",
            )
        )
        negative_fixture = p0.out(
            c,
            f"cd {p0.q(P)} && rg -n {p0.q('negative cumulative|cases_cumulative|CUMULATIVE_REVISION')} tests src/epidemic_core/normalization || true",
        ).splitlines()
        report_contract = json.loads(
            p0.out(c, f"cat {p0.q(P+'/reports/p1_4_who_normalization/p1_4_normalization_contract.json')}")
        )
        result = {
            "status": "BLOCKED",
            "gate": {
                "ssh": {
                    "key_type": trust["key_type"],
                    "stable_second_connection": True,
                    "independent_known_hosts": True,
                },
                "git": git,
                "alembic": alembic,
                "services": services,
                "formal_counts": counts,
            },
            "contract_metric_catalog": contract.get("metric_catalog", {}),
            "metric_seed_files": reference_files,
            "metric_seed_summary": seed_metrics,
            "database_metrics": database_metrics,
            "runtime_metrics": runtime,
            "p1_4_report_metric_catalog": report_contract.get("metric_catalog", {}),
            "negative_fixture_matches": len(negative_fixture),
            "reason": "P1_4_CONTRACT_DATABASE_RUNTIME_INCONSISTENCY",
            "remote_writes": False,
            "xmart_accessed": False,
        }
        print(json.dumps(result))
        if (
            git["head"] != EXPECTED_HEAD
            or git["branch"] != "main"
            or git["message"] != "p1-4: add deterministic WHO normalization pipeline"
            or not git["clean"]
            or git["remote_contains_head"]
            or alembic != "0003_p1_4_normalization_pipeline"
            or not all(services.values())
        ):
            raise p0.Blocked("P1_5_BASELINE_GATE_FAILED")
        cumulative_contract = [
            contract["metric_catalog"]["Cumulative_cases"],
            contract["metric_catalog"]["Cumulative_deaths"],
        ]
        cumulative_db = [x for x in database_metrics if x["metric_code"] in ("cases_cumulative", "deaths_cumulative")]
        cumulative_runtime = [x for x in runtime if x["metric_code"] in ("cases_cumulative", "deaths_cumulative")]
        inconsistent = any(not x["allow_negative"] or x["min_value"] not in (None, "0") for x in cumulative_contract)
        inconsistent = inconsistent or any(not x["allow_negative"] or x["min_value"] is not None for x in cumulative_db)
        inconsistent = inconsistent or any(not x["allow_negative"] or x["min_value"] is not None for x in cumulative_runtime)
        if inconsistent:
            raise p0.Blocked("P1_4_CONTRACT_DATABASE_RUNTIME_INCONSISTENCY")
    finally:
        c.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["hard-gate"])
    parser.parse_args()
    hard_gate()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("STATUS=BLOCKED")
        print("REASON=" + str(exc))
        raise SystemExit(1)

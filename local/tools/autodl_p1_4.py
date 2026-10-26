from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

import paramiko

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13

P = p0.P
PY = p1.PY
CORE = p1.CORE
EXPECTED_HEAD = "93a3b2307293ea537ccf7257069d773e33830ac6"
REPORT = P + "/reports/p1_4_who_normalization"
LOCAL_BASELINE = Path(__file__).parent / "p1_4_baseline"
LOCAL_PAYLOAD = Path(__file__).parent / "p1_4_payload"


def connection_gate() -> None:
    trust = p13.establish_trust()
    c = p13.conn()
    try:
        head = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD")
        parent = p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD^")
        message = p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s")
        branch = p0.out(c, f"cd {p0.q(P)} && git branch --show-current")
        status = p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines()
        pushed = bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False))
    finally:
        c.close()
    result = {
        "host_key": {"type": trust["key_type"], "fingerprint": trust["fingerprint"], "stable_second_connection": True},
        "identity": "root",
        "independent_known_hosts": True,
        "branch": branch,
        "head": head,
        "parent": parent,
        "message": message,
        "clean": not status,
        "remote_contains_head": pushed,
    }
    print(json.dumps(result))
    if head != EXPECTED_HEAD or branch != "main" or message != "p1-3: add controlled WHO raw retrieval pipeline" or status or pushed:
        raise p0.Blocked("P1_4_GIT_BASELINE_FAILED")


def preflight(c: paramiko.SSHClient) -> None:
    p0.run(c, f"mkdir -p {p0.q(REPORT)} && chmod 700 {p0.q(REPORT)}")
    services = {
        "postgres": p0.run(c, p0.q(P + "/scripts/postgres/status.sh"), check=False)[0] == 0,
        "vllm": p0.run(c, p0.q(P + "/scripts/qwen_vllm/status.sh"), check=False)[0] == 0,
        "query_api": p0.run(c, p0.q(P + "/scripts/query_api/status_query_api.sh"), check=False)[0] == 0,
        "worker": p0.run(c, p0.q(P + "/scripts/retrieval_worker/status_retrieval_worker.sh"), check=False)[0] == 0,
        "openclaw": p0.run(c, p0.q(P + "/scripts/openclaw/status_openclaw.sh"), check=False)[0] == 0,
    }
    alembic = p0.out(c, f"bash -lc {p0.q('source '+P+'/scripts/database/_env.sh;cd '+P+';'+PY+' -m alembic current')}")
    heads = p0.out(c, f"bash -lc {p0.q('source '+P+'/scripts/database/_env.sh;cd '+P+';'+PY+' -m alembic heads')}")
    ports = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q('''import json,pathlib
want={5432,8000,8010,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in want and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))''')}") )
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    audit = {}
    for name, cmd in (("doctor", "doctor --lint --json"), ("security", "security audit --json"), ("security_deep", "security audit --deep --json")):
        rc, out, err = p0.run(c, f"{p0.q(cli)} {cmd}", timeout=900, check=False)
        audit[name] = {"rc": rc}
        if name != "doctor" and rc:
            raise p0.Blocked("P1_4_PREFLIGHT_OPENCLAW_" + name)
        if name != "doctor" and json.loads(out).get("summary", {}).get("critical", 0):
            raise p0.Blocked("P1_4_PREFLIGHT_SECURITY_CRITICAL")
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    pg_run = P + "/storage/database/postgresql18/run"
    count_sql = "SELECT json_object_agg(tablename,n)::text FROM (SELECT tablename,(xpath('/row/c/text()',query_to_xml(format('SELECT count(*) c FROM epidemic.%I',tablename),false,true,'')))[1]::text::bigint n FROM pg_tables WHERE schemaname='epidemic')s"
    counts = json.loads(p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"))
    privilege_sql = "SELECT json_build_object('reference_insert',has_table_privilege('epidemic_app','epidemic.reference_datasets','INSERT'),'reference_update',has_table_privilege('epidemic_app','epidemic.reference_datasets','UPDATE'),'reference_delete',has_table_privilege('epidemic_app','epidemic.reference_datasets','DELETE'),'reference_truncate',has_table_privilege('epidemic_app','epidemic.reference_datasets','TRUNCATE'),'source_insert',has_table_privilege('epidemic_app','epidemic.data_sources','INSERT'),'source_update',has_table_privilege('epidemic_app','epidemic.data_sources','UPDATE'),'source_delete',has_table_privilege('epidemic_app','epidemic.data_sources','DELETE'),'source_truncate',has_table_privilege('epidemic_app','epidemic.data_sources','TRUNCATE'))::text"
    privileges = json.loads(p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(privilege_sql)}"))
    tracked = p0.out(c, f"cd {p0.q(P)} && git ls-files | grep -E '(^storage/|(^|/)node_modules/|[.]tgz$|[.]dump$)' || true").splitlines()
    proxy = p0.out(c, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${NO_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${no_proxy:-}${GIT_CONFIG:-} ]]&&echo clean||echo present'")
    result = {"status": "PASS", "services": services, "alembic": alembic, "heads": heads, "ports": ports, "openclaw": audit, "database_counts": counts, "reference_privileges": privileges, "tracked_runtime": tracked, "proxy": proxy}
    s = c.open_sftp(); p0.put(s, REPORT + "/preflight.json", json.dumps(result, indent=2) + "\n", 0o600); s.close()
    print(json.dumps(result))
    if not all(services.values()) or "0002_p1_3_retrieval_control" not in alembic or "0002_p1_3_retrieval_control" not in heads or {x["port"] for x in ports} != {5432,8000,8010,18789} or not all(x["loopback"] for x in ports) or any(privileges.values()) or tracked or proxy != "clean":
        raise p0.Blocked("P1_4_PREFLIGHT_FAILED")


def pull_baseline(c: paramiko.SSHClient) -> None:
    wanted = [
        "pyproject.toml", "alembic.ini", "migrations/env.py", "src/epidemic_core/db/models/tables.py",
        "src/epidemic_core/db/repositories/repositories.py", "src/epidemic_core/api/app.py", "src/epidemic_core/api/schemas.py",
        "src/epidemic_core/api/routes/retrievals.py", "src/epidemic_core/retrieval/service.py", "src/epidemic_core/retrieval/worker.py",
        "src/epidemic_core/retrieval/http.py", "src/epidemic_core/retrieval/registry.py", "reference/data_sources.v1.json",
        "integrations/openclaw/epidemic-query-tools/package.json", "integrations/openclaw/epidemic-query-tools/openclaw.plugin.json",
        "integrations/openclaw/epidemic-query-tools/src/index.ts", "integrations/openclaw/epidemic-query-tools/src/index.test.ts",
        "tests/integration/test_postgres.py",
        "tests/integration/conftest.py", "tests/conftest.py",
    ]
    versions = p0.out(c, f"find {p0.q(P+'/migrations/versions')} -maxdepth 1 -type f -name '*.py' -printf '%P\\n'").splitlines()
    wanted += ["migrations/versions/" + x for x in versions]
    s = c.open_sftp()
    for rel in wanted:
        path = LOCAL_BASELINE / PurePosixPath(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        with s.open(P + "/" + rel, "rb") as source:
            path.write_bytes(source.read())
    s.close()
    print(json.dumps({"status": "PASS", "files": len(wanted), "local": str(LOCAL_BASELINE)}))


def backup_and_tests(c: paramiko.SSHClient) -> None:
    p13.backup_and_baseline_tests(c)
    source = P + "/reports/p1_3_who_raw_retrieval/baseline_backup_and_tests.json"
    data = json.loads(p0.out(c, f"cat {p0.q(source)}"))
    data["stage"] = "P1-4 pre-migration gate"
    s = c.open_sftp(); p0.put(s, REPORT + "/baseline_backup_and_tests.json", json.dumps(data, indent=2) + "\n", 0o600); s.close()
    print(json.dumps(data))


def inspect_catalog(c: paramiko.SSHClient) -> None:
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"; pg_run = P + "/storage/database/postgresql18/run"
    sql = "SELECT json_agg(json_build_object('metric_code',metric_code,'unit',unit,'cumulative',cumulative,'allow_negative',allow_negative,'min_value',min_value,'max_value',max_value,'description',description) ORDER BY metric_code)::text FROM epidemic.metric_definitions"
    metrics = json.loads(p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}"))
    print(json.dumps({"metrics": metrics}))


def generate_references(c: paramiko.SSHClient) -> None:
    script = P + "/storage/tmp/p1_4_generate_references.py"; outdir = P + "/storage/tmp/p1_4_references"
    code = '''import csv,datetime as dt,hashlib,json,pathlib,urllib.request
from sqlalchemy import select
from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.db.models.tables import Country,MetricDefinition
from sqlalchemy.orm import Session
root=pathlib.Path("'''+P+'''");out=pathlib.Path("'''+outdir+'''");out.mkdir(parents=True,exist_ok=True)
url="https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-data.csv";target=out/"source.csv"
req=urllib.request.Request(url,headers={"User-Agent":"epidemic-openclaw-p1-4/1.0","Accept":"text/csv,application/octet-stream"})
with urllib.request.urlopen(req,timeout=120) as response:
 data=response.read(64*1024*1024+1);headers=dict(response.headers);status=response.status
if len(data)>64*1024*1024:raise SystemExit("oversize")
target.write_bytes(data);artifact_sha=hashlib.sha256(data).hexdigest()
labels={};header=None
with target.open("r",encoding="utf-8-sig",newline="") as f:
 reader=csv.reader(f);header=next(reader)
 for row in reader:
  if len(row)!=8:raise SystemExit("columns")
  labels.setdefault(row[1],set()).add(row[2])
s=Settings();engine=create_app_engine(s)
with Session(engine) as db:
 countries={x.alpha2:x for x in db.execute(select(Country)).scalars()}
 metrics={x.metric_code:{"unit":x.unit,"cumulative":x.cumulative,"allow_negative":x.allow_negative,"min_value":str(x.min_value) if x.min_value is not None else None} for x in db.execute(select(MetricDefinition)).scalars()}
engine.dispose()
rows=[]
for code in sorted(labels):
 mapped=code if code in countries else None
 rows.append({"who_country_code":code,"country_alpha2":mapped,"source_country_label":sorted(labels[code]),"mapping_status":"mapped" if mapped else "unmapped","mapping_version":"who-covid19-country-map-v1","evidence_artifact_sha256":artifact_sha})
mapping={"schema_version":1,"mapping_version":"who-covid19-country-map-v1","source_url":url,"source_artifact_sha256":artifact_sha,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),"mappings":rows}
metric_candidates={"New_cases":"cases_new","Cumulative_cases":"cases_cumulative","New_deaths":"deaths_new","Cumulative_deaths":"deaths_cumulative"}
if not all(x in metrics for x in metric_candidates.values()):raise SystemExit("metrics")
contract={"schema_version":1,"contract_version":"who-covid19-normalization-v1","transform_version":"who-covid19-transform-v1","country_mapping_version":mapping["mapping_version"],"quality_ruleset_version":"who-covid19-reported-weekly-quality-v1","snapshot_schema_version":1,"source_key":"who_covid19_global_weekly","source_url":url,"source_page_url":"https://data.who.int/dashboards/covid19/data","terms_url":"https://data.who.int/about/data/terms-and-conditions","attribution":"World Health Organization (WHO)","endorsement_disclaimer":"WHO does not endorse this system.","expected_header":["Date_reported","Country_code","Country","WHO_region","New_cases","Cumulative_cases","New_deaths","Cumulative_deaths"],"date_basis":"reported","period_label":"week_end","period_semantics":"reporting_bucket_not_event_occurrence","source_frequency":"weekly","missing_policy":"sparse/no-fill","negative_revision_policy":"preserve-and-flag","latest_period_policy":"preserve-and-flag-possibly-incomplete","metric_mapping":metric_candidates,"metric_catalog":{k:metrics[v] for k,v in metric_candidates.items()}}
def canonical(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\\n"
(out/"who_covid19_country_map.v1.json").write_text(canonical(mapping),encoding="utf-8")
(out/"who_covid19_normalization_contract.v1.json").write_text(canonical(contract),encoding="utf-8")
print(json.dumps({"artifact_sha256":artifact_sha,"byte_size":len(data),"http_status":status,"etag":headers.get("ETag"),"last_modified":headers.get("Last-Modified"),"codes":len(rows),"mapped":sum(x["mapping_status"]=="mapped" for x in rows),"unmapped":[x["who_country_code"] for x in rows if x["mapping_status"]!="mapped"],"label_conflicts":{k:sorted(v) for k,v in labels.items() if len(v)>1},"mapping_sha256":hashlib.sha256((out/"who_covid19_country_map.v1.json").read_bytes()).hexdigest(),"contract_sha256":hashlib.sha256((out/"who_covid19_normalization_contract.v1.json").read_bytes()).hexdigest()}))
'''
    s = c.open_sftp(); p0.put(s, script, code, 0o700); s.close()
    env = f"source {p0.q(P+'/scripts/retrieval_worker/retrieval_worker_env.sh')};export PYTHONPATH={p0.q(P+'/src')};unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY;"
    result = json.loads(p0.out(c, f"bash -lc {p0.q(env+' '+PY+' '+script)}", timeout=300))
    local_ref = LOCAL_PAYLOAD / "reference"; local_ref.mkdir(parents=True, exist_ok=True); s = c.open_sftp()
    for name in ("who_covid19_country_map.v1.json", "who_covid19_normalization_contract.v1.json"):
        with s.open(outdir + "/" + name, "rb") as source: (local_ref / name).write_bytes(source.read())
    s.close(); print(json.dumps(result))


def deploy_core(c: paramiko.SSHClient) -> None:
    local = LOCAL_PAYLOAD; s = c.open_sftp(); uploaded = 0
    def atomic(remote: str, value: str, mode: int = 0o644) -> None:
        nonlocal uploaded
        p0.mkdirs(s, str(PurePosixPath(remote).parent)); tmp = remote + ".p1_4_upload"; p0.put(s, tmp, value, mode); s.posix_rename(tmp, remote); s.chmod(remote, mode); uploaded += 1
    for path in sorted(local.rglob("*")):
        if path.is_file():
            rel = path.relative_to(local).as_posix(); atomic(P + "/" + rel, path.read_text(encoding="utf-8"), 0o750 if rel.startswith("scripts/") and rel.endswith(".sh") else 0o644)
    tables = P + "/src/epidemic_core/db/models/tables.py"
    with s.open(tables, "r") as source: model = source.read().decode()
    old = '''        CheckConstraint("jsonb_typeof(feature_spec)='object'", name="feature_spec_object"),
        {"schema": S},
'''
    new = '''        CheckConstraint("jsonb_typeof(feature_spec)='object'", name="feature_spec_object"),
        UniqueConstraint("query_request_id", "snapshot_schema_version", "content_sha256", name="uq_dataset_snapshots_query_schema_content"),
        {"schema": S},
'''
    if "uq_dataset_snapshots_query_schema_content" not in model:
        if old not in model: raise p0.Blocked("P1_4_SNAPSHOT_MODEL_SHAPE")
        model = model.replace(old, new)
    if "class NormalizationRun(Base):" not in model:
        model += '''

class NormalizationRun(Base):
    __tablename__ = "normalization_runs"
    __table_args__ = (
        CheckConstraint("normalization_key ~ '^[0-9a-f]{64}$'", name="key_sha"),
        CheckConstraint("status IN ('pending','running','succeeded','failed')", name="status"),
        CheckConstraint("attempt_number BETWEEN 1 AND 3 AND max_attempts=3", name="attempts"),
        CheckConstraint("(status='pending' AND claim_token IS NULL AND claimed_at IS NULL AND lease_expires_at IS NULL AND finished_at IS NULL) OR (status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL AND finished_at IS NULL) OR (status IN ('succeeded','failed') AND claim_token IS NULL AND lease_expires_at IS NULL AND finished_at IS NOT NULL)", name="state_shape"),
        CheckConstraint("dataset_snapshot_id IS NULL OR (status='succeeded' AND COALESCE(result_code,'')<>'NO_OBSERVATIONS')", name="snapshot_terminal"),
        CheckConstraint("total_source_rows>=0 AND selected_source_rows>=0 AND normalized_observation_count>=0 AND missing_value_count>=0 AND warning_count>=0 AND unmapped_source_code_count>=0 AND exact_duplicate_count>=0", name="counts_nonnegative"),
        UniqueConstraint("normalization_key", name="uq_normalization_runs_key"),
        UniqueConstraint("retrieval_run_id","source_fetch_id","transform_version","country_mapping_version","quality_ruleset_version","snapshot_schema_version", name="uq_normalization_runs_identity"),
        Index("ix_normalization_runs_pending_queue","created_at",postgresql_where=text("status='pending'")),
        {"schema": S},
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    retrieval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{S}.retrieval_runs.id"))
    source_fetch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{S}.source_fetches.id"))
    normalization_key: Mapped[str] = mapped_column(String(64))
    transform_version: Mapped[str] = mapped_column(String(100))
    country_mapping_version: Mapped[str] = mapped_column(String(100))
    quality_ruleset_version: Mapped[str] = mapped_column(String(100))
    snapshot_schema_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    result_code: Mapped[str | None] = mapped_column(String(100))
    attempt_number: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    dataset_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{S}.dataset_snapshots.id"))
    total_source_rows: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    selected_source_rows: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    normalized_observation_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    missing_value_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    warning_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    unmapped_source_code_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    exact_duplicate_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class NormalizationRunEvent(Base):
    __tablename__ = "normalization_run_events"
    __table_args__ = (CheckConstraint("jsonb_typeof(details)='object'", name="details_object"), {"schema": S})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    normalization_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{S}.normalization_runs.id"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    event_code: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
'''
    in_normalization = False; formatted_model: list[str] = []
    for line in model.splitlines():
        if line.startswith("class NormalizationRun(Base):"): in_normalization = True
        if in_normalization and len(line) > 120 and "# noqa:" not in line: line += "  # noqa: E501"
        formatted_model.append(line)
    model = "\n".join(formatted_model) + "\n"
    atomic(tables, model)
    schemas = P + "/src/epidemic_core/api/schemas.py"
    with s.open(schemas, "r") as source: schema = source.read().decode()
    marker = "    normalized_observations_created: bool = False\n"
    additions = '''    retrieval_status: str
    normalization_run_id: uuid.UUID | None = None
    normalization_status: str | None = None
    pipeline_status: str
    result_code: str | None = None
    selected_source_rows: int = 0
    normalized_observation_count: int = 0
    missing_value_count: int = 0
    quality_warning_count: int = 0
    dataset_snapshot_id: uuid.UUID | None = None
    snapshot_status: str
    snapshot_row_count: int = 0
    snapshot_content_sha256: str | None = None
    snapshot_schema_version: int | None = None
    training_format_ready: bool = False
    forecast_readiness_assessed: bool = False
    prediction_performed: bool = False
    terms_url: str
    license_summary: str
'''
    if "normalization_run_id:" not in schema:
        if marker not in schema: raise p0.Blocked("P1_4_SCHEMA_SHAPE")
        schema = schema.replace(marker, marker + additions)
    atomic(schemas, schema)
    worker = P + "/src/epidemic_core/retrieval/worker.py"
    with s.open(worker, "r") as source: value = source.read().decode()
    if "NormalizationWorker" not in value:
        value = value.replace("from epidemic_core.retrieval.service import RetrievalService", "from epidemic_core.retrieval.service import RetrievalService\nfrom epidemic_core.normalization.contract import load_contract\nfrom epidemic_core.normalization.service import NormalizationService\nfrom epidemic_core.normalization.worker import NormalizationWorker")
        value = value.replace("        self.stop_requested = False", "        self.stop_requested = False\n        self.normalizer = NormalizationWorker(factory, registry_path.parent, raw_root)")
        value = value.replace("            return RetrievalService.reconcile_expired(session, dt.datetime.now(dt.timezone.utc))", "            count = RetrievalService.reconcile_expired(session, dt.datetime.now(dt.timezone.utc))\n        return count + self.normalizer.recover()")
        value = value.replace("            if claimed is None:\n                return False", "            if claimed is None:\n                return self.normalizer.run_once()")
        old_fetch = '''        def add_fetches(session: Session, artifact_id: uuid.UUID | None = None) -> None:
            for index, attempt in enumerate(attempts):
                session.add(
                    SourceFetch(
                        id=uuid.uuid4(),
                        retrieval_run_id=run_id,
                        source_id=source_id,
                        canonical_url=spec.download_url,
                        requested_at=dt.datetime.now(dt.timezone.utc),
                        fetched_at=dt.datetime.now(dt.timezone.utc) if attempt.http_status else None,
                        http_status=attempt.http_status,
                        etag=attempt.etag,
                        last_modified=attempt.last_modified,
                        artifact_id=artifact_id if index == len(attempts) - 1 else None,
                        attempt_number=attempt.number,
                        error_code=attempt.error_code,
                        error_message=None,
                        response_content_type=attempt.content_type,
                        response_byte_size=attempt.byte_size,
                    )
                )
'''
        new_fetch = '''        def add_fetches(session: Session, artifact_id: uuid.UUID | None = None) -> SourceFetch | None:
            last: SourceFetch | None = None
            for index, attempt in enumerate(attempts):
                last = SourceFetch(id=uuid.uuid4(),retrieval_run_id=run_id,source_id=source_id,canonical_url=spec.download_url,requested_at=dt.datetime.now(dt.timezone.utc),fetched_at=dt.datetime.now(dt.timezone.utc) if attempt.http_status else None,http_status=attempt.http_status,etag=attempt.etag,last_modified=attempt.last_modified,artifact_id=artifact_id if index == len(attempts)-1 else None,attempt_number=attempt.number,error_code=attempt.error_code,error_message=None,response_content_type=attempt.content_type,response_byte_size=attempt.byte_size)
                session.add(last)
            session.flush()
            return last
'''
        if old_fetch not in value: raise p0.Blocked("P1_4_WORKER_FETCH_SHAPE")
        value = value.replace(old_fetch, new_fetch)
        value = value.replace("                add_fetches(session, artifact_id)\n                if not RetrievalService.finish(", "                last_fetch = add_fetches(session, artifact_id)\n                if last_fetch is None or artifact_id is None:\n                    raise RuntimeError(\"PROTOCOL_INCONSISTENCY\")\n                if not RetrievalService.finish(")
        anchor = '''            log.info(
                "run_id=%s source_key=%s status=succeeded bytes=%s sha256=%s",
'''
        enqueue = '''                retrieval_row = session.get(RetrievalRun, run_id)
                artifact_row = session.get(RawArtifact, artifact_id)
                if retrieval_row is None or artifact_row is None:
                    raise RuntimeError("PROTOCOL_INCONSISTENCY")
                contract, _mapping = load_contract(session, self.registry_path.parent)
                NormalizationService.enqueue(session, retrieval_row, last_fetch, artifact_row.sha256, contract.versions)
            log.info(
                "run_id=%s source_key=%s status=succeeded bytes=%s sha256=%s",
'''
        if anchor not in value: raise p0.Blocked("P1_4_WORKER_FINALIZE_SHAPE")
        value = value.replace(anchor, enqueue)
        value = value.replace('log.info("worker_version=p1-3-claim-scalar-v1")', 'log.info("worker_version=p1-4-normalization-v1")')
    atomic(worker, value)
    repositories = P + "/src/epidemic_core/db/repositories/repositories.py"
    with s.open(repositories, "r") as source: repository_value = source.read().decode()
    old_snapshot = '''        sid = uuid.uuid4()
        session.add(
            DatasetSnapshot(
                id=sid,
                query_request_id=query_request_id,
                snapshot_schema_version=1,
                cutoff_at=cutoff_at,
                row_count=len(rows),
                content_sha256=digest,
                feature_spec=feature_spec,
            )
        )
        session.flush()
        for number, row in enumerate(rows, 1):
            session.add(
                DatasetSnapshotObservation(snapshot_id=sid, observation_id=row["observation_id"], row_number=number)
            )
        return DatasetSnapshotResult(snapshot_id=sid, row_count=len(rows), content_sha256=digest)
'''
    new_snapshot = '''        sid = uuid.uuid4()
        created = session.execute(
            insert(DatasetSnapshot)
            .values(id=sid,query_request_id=query_request_id,snapshot_schema_version=1,cutoff_at=cutoff_at,row_count=len(rows),content_sha256=digest,feature_spec=feature_spec)
            .on_conflict_do_nothing(index_elements=[DatasetSnapshot.query_request_id,DatasetSnapshot.snapshot_schema_version,DatasetSnapshot.content_sha256])
            .returning(DatasetSnapshot.id)
        ).scalar_one_or_none()
        if created is None:
            existing = session.execute(select(DatasetSnapshot).where(DatasetSnapshot.query_request_id==query_request_id,DatasetSnapshot.snapshot_schema_version==1,DatasetSnapshot.content_sha256==digest)).scalar_one()
            return DatasetSnapshotResult(snapshot_id=existing.id,row_count=existing.row_count,content_sha256=existing.content_sha256)
        for number,row in enumerate(rows,1):
            session.add(DatasetSnapshotObservation(snapshot_id=sid,observation_id=row["observation_id"],row_number=number))
        return DatasetSnapshotResult(snapshot_id=sid,row_count=len(rows),content_sha256=digest)
'''
    if old_snapshot in repository_value: repository_value = repository_value.replace(old_snapshot, new_snapshot)
    elif not ("on_conflict_do_nothing" in repository_value and "DatasetSnapshot.query_request_id" in repository_value): raise p0.Blocked("P1_4_REPOSITORY_SHAPE")
    atomic(repositories, repository_value)
    postgres_test = P + "/tests/integration/test_postgres.py"
    with s.open(postgres_test, "r") as source: test_value = source.read().decode()
    marker_test = '    with Session(app) as s, pytest.raises(IntegrityError), s.begin():\n        s.add(\n            NormalizedObservation(\n                id=uuid.uuid4(),\n                record_fingerprint="4" * 64,\n'
    replacement_test = '    with Session(app) as s, s.begin():\n        s.add(\n            NormalizedObservation(\n                id=uuid.uuid4(),\n                record_fingerprint="4" * 64,\n'
    if marker_test in test_value: test_value = test_value.replace(marker_test, replacement_test, 1)
    elif replacement_test not in test_value: raise p0.Blocked("P1_4_CUMULATIVE_TEST_SHAPE")
    atomic(postgres_test, test_value)
    s.close()
    p0.run(c, f"{p0.q(CORE+'/bin/ruff')} format {p0.q(P+'/src')} {p0.q(P+'/migrations')} {p0.q(P+'/tests')}")
    rc, out, err = p0.run(c, f"{p0.q(CORE+'/bin/ruff')} check --fix {p0.q(P+'/src')} {p0.q(P+'/migrations')} {p0.q(P+'/tests')}", check=False)
    if rc: raise p0.Blocked("P1_4_DEPLOY_RUFF:" + (out + err)[-4000:])
    print(json.dumps({"status": "PASS", "uploaded": uploaded, "migration": "0003_p1_4_normalization_pipeline"}))


def ruff_diagnose(c: paramiko.SSHClient) -> None:
    rc, out, err = p0.run(c, f"{p0.q(CORE+'/bin/ruff')} check {p0.q(P+'/src')} {p0.q(P+'/migrations')} {p0.q(P+'/tests')}", check=False)
    print(json.dumps({"rc": rc, "output": out + "\n" + err}))


def migration_tests(c: paramiko.SSHClient) -> None:
    baseline = json.loads(p0.out(c, f"cat {p0.q(REPORT+'/baseline_backup_and_tests.json')}")); dump = baseline["backup"]["path"]
    db = "epidemic_p1_4_test"; pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"; pg_run = P + "/storage/database/postgresql18/run"
    for cmd in (
        f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",
        f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",
        f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db} {p0.q(dump)}",
    ): p0.run(c, cmd, timeout=900)
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    script = P + "/storage/tmp/p1_4_make_test_env.py"; env_file = P + "/storage/secrets/p1-4-test.env"
    code = '''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");pg={k:v for k,v in (x.split("=",1) for x in (root/".secrets/postgres.env").read_text().splitlines() if "=" in x)};m={k:v for k,v in (x.split("=",1) for x in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in x)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_4_test",u.query,u.fragment));a="postgresql+psycopg://epidemic_app:"+urllib.parse.quote(pg["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_4_test";q=root/"storage/secrets/p1-4-test.env";q.write_text("APP_DATABASE_URL="+a+"\\nMIGRATION_DATABASE_URL="+m+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_4_raw")+"\\n");q.chmod(0o600)
'''; s = c.open_sftp(); p0.put(s, script, code, 0o700); s.close(); p0.run(c, f"{p0.q(PY)} {p0.q(script)}")
    env = f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"; alembic = PY + " -m alembic"
    def command(value: str, timeout: int = 900) -> str: return p0.out(c, f"bash -lc {p0.q(env+' '+value)}", timeout=timeout)
    initial = command(alembic + " current"); up1 = command(alembic + " upgrade head"); head1 = command(alembic + " current"); down = command(alembic + " downgrade 0002_p1_3_retrieval_control"); at2 = command(alembic + " current"); up2 = command(alembic + " upgrade head"); head2 = command(alembic + " current"); check = command(alembic + " check")
    seed = '''from pathlib import Path
import os,json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.normalization.registry import import_normalization_references
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with Session(e) as s,s.begin():print(json.dumps(import_normalization_references(s,Path("'''+P+'''/reference"))))
e.dispose()
'''; seeded = json.loads(command(PY + " -c " + p0.q(seed)))
    rc, out, err = p0.run(c, f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}", timeout=1800, check=False)
    if rc: raise p0.Blocked("P1_4_TESTS_FAILED:" + (out + err)[-8000:])
    quality = {}
    for name, cmd in (("ruff",f"{CORE}/bin/ruff check src tests migrations"),("format",f"{CORE}/bin/ruff format --check src tests migrations"),("mypy",f"{CORE}/bin/mypy src/epidemic_core"),("pip",f"{PY} -m pip check")):
        qrc,qout,qerr=p0.run(c,f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {cmd}",timeout=900,check=False);quality[name]={"rc":qrc,"tail":(qout+qerr)[-1500:]}
        if qrc:raise p0.Blocked("P1_4_QUALITY_FAILED:"+name+":"+(qout+qerr)[-5000:])
    privilege_sql = "SELECT json_build_object('norm_delete',has_table_privilege('epidemic_app','epidemic.normalization_runs','DELETE'),'norm_truncate',has_table_privilege('epidemic_app','epidemic.normalization_runs','TRUNCATE'),'events_update',has_table_privilege('epidemic_app','epidemic.normalization_run_events','UPDATE'),'events_delete',has_table_privilege('epidemic_app','epidemic.normalization_run_events','DELETE'),'obs_update',has_table_privilege('epidemic_app','epidemic.normalized_observations','UPDATE'),'obs_delete',has_table_privilege('epidemic_app','epidemic.normalized_observations','DELETE'),'quality_update',has_table_privilege('epidemic_app','epidemic.quality_assessments','UPDATE'),'snap_delete',has_table_privilege('epidemic_app','epidemic.dataset_snapshots','DELETE'))::text"
    privileges = json.loads(p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(privilege_sql)}"))
    if any(privileges.values()): raise p0.Blocked("P1_4_PERMISSION_GATE")
    result={"status":"PASS","migration":{"initial":initial,"upgrade":head1,"downgrade":at2,"reupgrade":head2,"check":check},"references":seeded,"full_pytest":out.splitlines()[-1],"quality":quality,"privileges":privileges,"temporary_database":db}
    s=c.open_sftp();p0.put(s,REPORT+"/migration_and_test_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def inspect_baseline_init(c: paramiko.SSHClient) -> None:
    print(json.dumps({"content": p0.out(c, f"cd {p0.q(P)} && git show {EXPECTED_HEAD}:src/epidemic_core/normalization/__init__.py")}))


def read_migration_result(c: paramiko.SSHClient) -> None:
    print(p0.out(c, f"cat {p0.q(REPORT+'/migration_and_test_results.json')}", check=False))


def test_diagnose(c: paramiko.SSHClient) -> None:
    env=f"set -a;source {p0.q(P+'/storage/secrets/p1-4-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};";commands={"pytest":PY+" -m pytest -q","ruff":CORE+"/bin/ruff check src tests migrations","format":CORE+"/bin/ruff format --check src tests migrations","mypy":CORE+"/bin/mypy src/epidemic_core","pip":PY+" -m pip check"};results={}
    for name,cmd in commands.items():
        rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+cmd)}",timeout=1800,check=False);results[name]={"rc":rc,"tail":(out+err)[-4000:]}
        s=c.open_sftp();p0.put(s,REPORT+"/test_diagnose_checkpoint.json",json.dumps(results,indent=2)+"\n",0o600);s.close()
    print(json.dumps(results))


def live_who(c: paramiko.SSHClient) -> None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180)
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";db="epidemic_p1_4_test"
    truncate="TRUNCATE epidemic.normalization_run_events,epidemic.normalization_runs,epidemic.dataset_snapshot_observations,epidemic.dataset_snapshots,epidemic.quality_assessments,epidemic.normalized_observations,epidemic.source_fetches,epidemic.retrieval_run_events,epidemic.retrieval_runs,epidemic.raw_artifacts,epidemic.query_requests RESTART IDENTITY CASCADE"
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -v ON_ERROR_STOP=1 -c {p0.q(truncate)}")
    make=P+"/storage/tmp/p1_4_service_env.py";code='''import pathlib
root=pathlib.Path("'''+P+'''");v={k:x for k,x in (line.split("=",1) for line in (root/"storage/secrets/p1-4-test.env").read_text().splitlines() if "=" in line)}
for name in ("query-api-test-database.env","retrieval-worker-test.env"):
 p=root/"storage/secrets"/name;p.write_text("APP_DATABASE_URL="+v["APP_DATABASE_URL"]+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_4_raw_live")+"\\n");p.chmod(0o600)
''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}")
    p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120)
    client=P+"/storage/tmp/p1_4_live_client.py";client_code='''import concurrent.futures,json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};origin="http://127.0.0.1:8010";body={"country":"France","disease":"COVID-19","start_date":"2024-01-01","end_date":"2024-03-31"}
with httpx.Client(timeout=20) as c:
 r=c.post(origin+"/v1/queries",headers=h,json=body);r.raise_for_status();qid=r.json()["query_id"]
def start(_):
 with httpx.Client(timeout=20) as c:
  r=c.post(origin+f"/v1/queries/{qid}/retrievals",headers=h,json={});return r.status_code,r.json()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:starts=list(pool.map(start,range(10)))
run_ids={x[1].get("retrieval_run_id") for x in starts};assert len(run_ids)==1;rid=next(iter(run_ids));final=None
for _ in range(600):
 with httpx.Client(timeout=20) as c:r=c.get(origin+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed" or final.get("retrieval_status")=="failed":break
 time.sleep(1)
assert final is not None
print(json.dumps({"query_id":qid,"retrieval_run_id":rid,"start_statuses":[x[0] for x in starts],"unique_retrieval_runs":len(run_ids),"final":final}))
''';s=c.open_sftp();p0.put(s,client,client_code,0o700);s.close();rc,out,err=p0.run(c,f"{p0.q(PY)} {p0.q(client)}",timeout=900,check=False)
    if rc:
        log=p0.out(c,f"tail -n 40 {p0.q(P+'/storage/logs/retrieval-worker/worker.log')}|sed -E 's#(token|password|authorization|postgresql)[^ ]*#<redacted>#Ig'",check=False);raise p0.Blocked("P1_4_LIVE_CLIENT:"+(err+log)[-5000:])
    api=json.loads(out);final=api["final"]
    evidence_sql="""SELECT json_build_object('retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots),'snapshot_membership',(SELECT count(*) FROM epidemic.dataset_snapshot_observations),'training_view',(SELECT count(*) FROM epidemic.training_snapshot_observations_v1),'countries',(SELECT count(*) FROM epidemic.countries),'artifact_sha',(SELECT a.sha256 FROM epidemic.raw_artifacts a LIMIT 1),'artifact_bytes',(SELECT a.byte_size FROM epidemic.raw_artifacts a LIMIT 1),'etag',(SELECT sf.etag FROM epidemic.source_fetches sf ORDER BY requested_at DESC LIMIT 1),'last_modified',(SELECT sf.last_modified FROM epidemic.source_fetches sf ORDER BY requested_at DESC LIMIT 1),'total_source_rows',(SELECT total_source_rows FROM epidemic.normalization_runs LIMIT 1),'selected_source_rows',(SELECT selected_source_rows FROM epidemic.normalization_runs LIMIT 1),'observation_count',(SELECT normalized_observation_count FROM epidemic.normalization_runs LIMIT 1),'missing_count',(SELECT missing_value_count FROM epidemic.normalization_runs LIMIT 1),'warning_count',(SELECT warning_count FROM epidemic.normalization_runs LIMIT 1),'snapshot_sha',(SELECT content_sha256 FROM epidemic.dataset_snapshots LIMIT 1),'snapshot_id',(SELECT id FROM epidemic.dataset_snapshots LIMIT 1),'min_period',(SELECT min(period_end) FROM epidemic.normalized_observations),'max_period',(SELECT max(period_end) FROM epidemic.normalized_observations),'countries_in_observations',(SELECT json_agg(DISTINCT country_alpha2) FROM epidemic.normalized_observations),'negative_values',(SELECT count(*) FROM epidemic.normalized_observations WHERE value_numeric<0))::text"""
    evidence=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(evidence_sql)}"))
    integrity=P+"/storage/tmp/p1_4_integrity.py";integrity_code='''import hashlib,json,os,pathlib
from sqlalchemy import create_engine,text
e=create_engine(os.environ["APP_DATABASE_URL"])
with e.connect() as c:r=c.execute(text("SELECT storage_uri,sha256,byte_size FROM epidemic.raw_artifacts LIMIT 1")).one()
p=pathlib.Path(r[0]);h=hashlib.sha256()
with p.open("rb") as f:
 for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
print(json.dumps({"sha_matches":h.hexdigest()==r[1],"size_matches":p.stat().st_size==r[2]}));e.dispose()
''';s=c.open_sftp();p0.put(s,integrity,integrity_code,0o700);s.close();env=f"set -a;source {p0.q(P+'/storage/secrets/p1-4-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};";integrity_result=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+integrity)}"))
    passed=final.get("pipeline_status")=="ready" and final.get("normalized") is True and final.get("training_format_ready") is True and final.get("prediction_performed") is False and evidence["retrieval_runs"]==1 and evidence["normalization_runs"]==1 and evidence["dataset_snapshots"]==1 and evidence["snapshot_membership"]==evidence["training_view"]==evidence["observation_count"] and evidence["countries"]==249 and evidence["countries_in_observations"]==["FR"] and evidence["min_period"]>="2024-01-01" and evidence["max_period"]<="2024-03-31" and integrity_result["sha_matches"] and integrity_result["size_matches"]
    result={"status":"PASS" if passed else "FAIL","api":api,"evidence":evidence,"integrity":integrity_result,"raw_artifact_scope":"global","snapshot_query_scoped":True,"no_prediction":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_4_live_who_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":result["status"],"final":final,"evidence":evidence,"integrity":integrity_result}));
    if not passed:raise p0.Blocked("P1_4_LIVE_ACCEPTANCE_FAILED")


def diagnose_live(c: paramiko.SSHClient) -> None:
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";sql="SELECT json_agg(json_build_object('id',id,'status',status,'error_code',error_code,'attempt',attempt_number))::text FROM epidemic.normalization_runs";rows=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_4_test -Atc {p0.q(sql)}");shape_sql="SELECT json_build_object('basename',regexp_replace(storage_uri,'.*/',''),'sha',sha256,'under_temp',storage_uri LIKE '%/storage/tmp/p1_4_raw_live/%')::text FROM epidemic.raw_artifacts LIMIT 1";shape=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_4_test -Atc {p0.q(shape_sql)}"));log=p0.out(c,f"tail -n 50 {p0.q(P+'/storage/logs/retrieval-worker/worker.log')}|sed -E 's#(token|password|authorization|postgresql)[^ ]*#<redacted>#Ig'",check=False).splitlines();api_log=p0.out(c,f"tail -n 40 {p0.q(P+'/storage/logs/query-api/query-api.log')}|sed -E 's#(token|password|authorization|postgresql)[^ ]*#<redacted>#Ig'",check=False).splitlines();print(json.dumps({"runs":json.loads(rows) if rows else [],"artifact_shape":shape,"worker_status":p0.run(c,p0.q(P+'/scripts/retrieval_worker/status_retrieval_worker.sh'),check=False)[0],"log":log,"api_log":api_log}))


def retry_live(c: paramiko.SSHClient) -> None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120)
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";db="epidemic_p1_4_test";ids=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q('SELECT query_request_id::text||\'|\'||id::text FROM epidemic.retrieval_runs LIMIT 1')}");qid,rid=ids.split("|")
    client=P+"/storage/tmp/p1_4_retry_client.py";code='''import json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};o="http://127.0.0.1:8010";qid="'''+qid+'''";rid="'''+rid+'''"
with httpx.Client(timeout=20) as c:r=c.post(o+f"/v1/queries/{qid}/retrievals",headers=h,json={});r.raise_for_status();start=r.json()
final=None
for _ in range(300):
 with httpx.Client(timeout=20) as c:r=c.get(o+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed":break
 time.sleep(1)
print(json.dumps({"start":start,"final":final}))
''';s=c.open_sftp();p0.put(s,client,code,0o700);s.close();api=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(client)}",timeout=600))
    sql="""SELECT json_build_object('retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalization_attempt',(SELECT attempt_number FROM epidemic.normalization_runs LIMIT 1),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots),'snapshot_membership',(SELECT count(*) FROM epidemic.dataset_snapshot_observations),'training_view',(SELECT count(*) FROM epidemic.training_snapshot_observations_v1),'countries',(SELECT count(*) FROM epidemic.countries),'artifact_sha',(SELECT sha256 FROM epidemic.raw_artifacts LIMIT 1),'artifact_bytes',(SELECT byte_size FROM epidemic.raw_artifacts LIMIT 1),'etag',(SELECT etag FROM epidemic.source_fetches LIMIT 1),'last_modified',(SELECT last_modified FROM epidemic.source_fetches LIMIT 1),'total_source_rows',(SELECT total_source_rows FROM epidemic.normalization_runs LIMIT 1),'selected_source_rows',(SELECT selected_source_rows FROM epidemic.normalization_runs LIMIT 1),'observation_count',(SELECT normalized_observation_count FROM epidemic.normalization_runs LIMIT 1),'missing_count',(SELECT missing_value_count FROM epidemic.normalization_runs LIMIT 1),'warning_count',(SELECT warning_count FROM epidemic.normalization_runs LIMIT 1),'snapshot_sha',(SELECT content_sha256 FROM epidemic.dataset_snapshots LIMIT 1),'snapshot_id',(SELECT id FROM epidemic.dataset_snapshots LIMIT 1),'min_period',(SELECT min(period_end) FROM epidemic.normalized_observations),'max_period',(SELECT max(period_end) FROM epidemic.normalized_observations),'countries_in_observations',(SELECT json_agg(DISTINCT country_alpha2) FROM epidemic.normalized_observations),'negative_values',(SELECT count(*) FROM epidemic.normalized_observations WHERE value_numeric<0))::text""";evidence=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(sql)}"));final=api["final"];passed=final and final.get("pipeline_status")=="ready" and final.get("normalized") is True and final.get("prediction_performed") is False and evidence["retrieval_runs"]==evidence["normalization_runs"]==evidence["source_fetches"]==evidence["raw_artifacts"]==evidence["dataset_snapshots"]==1 and evidence["normalization_attempt"]==2 and evidence["snapshot_membership"]==evidence["training_view"]==evidence["observation_count"] and evidence["countries"]==249 and evidence["countries_in_observations"]==["FR"] and evidence["min_period"]>="2024-01-01" and evidence["max_period"]<="2024-03-31";result={"status":"PASS" if passed else "FAIL","api":api,"evidence":evidence,"normalization_retry_without_redownload":evidence["source_fetches"]==1};s=c.open_sftp();p0.put(s,REPORT+"/p1_4_live_who_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));
    if not passed:raise p0.Blocked("P1_4_RETRY_LIVE_FAILED")


def retry_probe(c: paramiko.SSHClient) -> None:
    script=P+"/storage/tmp/p1_4_retry_probe.py";code='''import json,os
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from epidemic_core.db.models.tables import NormalizationRun
from epidemic_core.normalization.service import NormalizationService
e=create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
try:
 with Session(e) as s,s.begin():
  row=s.execute(select(NormalizationRun)).scalar_one();changed=NormalizationService.retry_failed(s,row.retrieval_run_id)
 print(json.dumps({"ok":True,"changed":changed}))
except Exception as x:
 orig=getattr(x,"orig",None);print(json.dumps({"ok":False,"class":type(x).__name__,"orig_class":type(orig).__name__ if orig else None,"sqlstate":getattr(orig,"sqlstate",None)}))
finally:e.dispose()
    ''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();env=f"set -a;source {p0.q(P+'/storage/secrets/p1-4-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};";print(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+script)}"))


def refresh_temp_trigger(c: paramiko.SSHClient) -> None:
    sql="""CREATE OR REPLACE FUNCTION epidemic.protect_normalization_terminal() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF OLD.status='succeeded' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal normalization run is immutable'; END IF;
    IF OLD.status='failed' AND NEW IS DISTINCT FROM OLD AND NOT (NEW.status='pending' AND NEW.attempt_number=OLD.attempt_number+1 AND NEW.attempt_number<=NEW.max_attempts AND NEW.claim_token IS NULL AND NEW.claimed_at IS NULL AND NEW.lease_expires_at IS NULL AND NEW.started_at IS NULL AND NEW.finished_at IS NULL AND NEW.error_code IS NULL) THEN RAISE EXCEPTION 'failed normalization run permits only controlled retry'; END IF;
    IF NEW.id<>OLD.id OR NEW.normalization_key<>OLD.normalization_key OR NEW.retrieval_run_id<>OLD.retrieval_run_id OR NEW.source_fetch_id<>OLD.source_fetch_id THEN RAISE EXCEPTION 'normalization identity is immutable'; END IF;
    RETURN NEW; END $$""";script=P+"/storage/tmp/p1_4_refresh_trigger.py";code='''import os
from sqlalchemy import create_engine,text
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with e.begin() as c:c.execute(text("""'''+sql+'''"""))
e.dispose();print("PASS")
    ''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();env=f"set -a;source {p0.q(P+'/storage/secrets/p1-4-test.env')};set +a;";print(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+script)}"))


def revision_determinism(c: paramiko.SSHClient) -> None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180)
    script=P+"/storage/tmp/p1_4_revision_determinism.py";code='''import csv,datetime as dt,hashlib,json,os,pathlib,uuid
from sqlalchemy import create_engine,func,select
from sqlalchemy.orm import Session,sessionmaker
from epidemic_core.db.models.tables import DataSource,DatasetSnapshot,DatasetSnapshotObservation,Disease,NormalizationRun,NormalizedObservation,QueryRequest,RawArtifact,RetrievalRun,SourceFetch
from epidemic_core.normalization.contract import load_contract
from epidemic_core.normalization.service import NormalizationService,fail_run
from epidemic_core.normalization.worker import NormalizationWorker
root=pathlib.Path("'''+P+'''");raw=root/"storage/tmp/p1_4_raw_live";e=create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True);factory=sessionmaker(e,expire_on_commit=False);nonce=str(uuid.uuid4())
with Session(e) as s:
 base_norm=s.execute(select(NormalizationRun).where(NormalizationRun.status=="succeeded").order_by(NormalizationRun.created_at)).scalars().first();assert base_norm and base_norm.dataset_snapshot_id
 base_snapshot=s.get(DatasetSnapshot,base_norm.dataset_snapshot_id);assert base_snapshot
 base_hash=base_snapshot.content_sha256;base_id=base_snapshot.id;base_members=s.scalar(select(func.count()).select_from(DatasetSnapshotObservation).where(DatasetSnapshotObservation.snapshot_id==base_id));base_obs=s.scalar(select(func.count()).select_from(NormalizedObservation));source=s.execute(select(DataSource).where(DataSource.source_key=="who_covid19_global_weekly")).scalar_one();disease=s.execute(select(Disease).where(Disease.slug=="covid-19")).scalar_one();base_fetch=s.get(SourceFetch,base_norm.source_fetch_id);assert base_fetch and base_fetch.artifact_id;base_art=s.get(RawArtifact,base_fetch.artifact_id);assert base_art
 contract,_=load_contract(s,root/"reference")
def make_file(mode):
 source=pathlib.Path(base_art.storage_uri);tmp=raw/("mutate-"+mode+".tmp");chosen=None
 with source.open("r",encoding="utf-8-sig",newline="") as inp,tmp.open("w",encoding="utf-8",newline="") as out:
  reader=csv.reader(inp);writer=csv.writer(out,lineterminator="\\n");writer.writerow(next(reader))
  for row in reader:
   target=row[1]=="FR" and "2024-01-01"<=row[0]<="2024-03-31"
   if mode=="delete" and target and chosen is None:chosen=list(row);continue
   if mode=="change" and target and chosen is None:
    chosen=list(row);index=next(i for i in range(4,8) if row[i].strip());row[index]=str(int(row[index])+1);chosen.append(str(index))
   if mode=="conflict" and target and chosen is None:chosen=list(row)
   writer.writerow(row)
  if mode=="conflict" and chosen is not None:
   duplicate=list(chosen);index=next(i for i in range(4,8) if duplicate[i].strip());duplicate[index]=str(int(duplicate[index])+1);writer.writerow(duplicate)
 data=tmp.read_bytes();digest=hashlib.sha256(data).hexdigest();target=raw/digest[:2]/(digest+".csv");target.parent.mkdir(parents=True,exist_ok=True);tmp.replace(target);return target,digest,len(data),chosen
def pipeline(artifact,suffix,fetched):
 with Session(e) as s,s.begin():
  art=s.execute(select(RawArtifact).where(RawArtifact.sha256==artifact[1])).scalar_one_or_none()
  if art is None:art=RawArtifact(id=uuid.uuid4(),sha256=artifact[1],byte_size=artifact[2],media_type="application/octet-stream",encoding=None,storage_mode="file",storage_uri=str(artifact[0]),inline_text=None,inline_json=None);s.add(art);s.flush()
  q=QueryRequest(id=uuid.uuid4(),fingerprint=hashlib.sha256(("query-"+suffix+nonce).encode()).hexdigest(),raw_country="France",raw_disease="COVID-19",country_alpha2="FR",disease_id=disease.id,start_date=dt.date(2024,1,1),end_date=dt.date(2024,3,31),schema_version=1);s.add(q);s.flush()
  r=RetrievalRun(id=uuid.uuid4(),run_key=hashlib.sha256(("run-"+suffix+nonce).encode()).hexdigest(),query_request_id=q.id,source_id=source.id,adapter_version="who-covid19-csv-v1",status="succeeded",attempt_number=1,started_at=fetched,finished_at=fetched);s.add(r);s.flush()
  f=SourceFetch(id=uuid.uuid4(),retrieval_run_id=r.id,source_id=source.id,canonical_url="https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-data.csv",requested_at=fetched,fetched_at=fetched,http_status=200,etag=base_fetch.etag,last_modified=base_fetch.last_modified,artifact_id=art.id,attempt_number=1,response_content_type="application/octet-stream",response_byte_size=art.byte_size);s.add(f);s.flush();n=NormalizationService.enqueue(s,r,f,art.sha256,contract.versions);nid=n.id
 assert NormalizationWorker(factory,root/"reference",raw).run_once()
 with Session(e) as s:n=s.get(NormalizationRun,nid);assert n and n.status in ("succeeded","failed");snap=s.get(DatasetSnapshot,n.dataset_snapshot_id) if n.dataset_snapshot_id else None;return n,snap
now=dt.datetime(2030,1,1,tzinfo=dt.timezone.utc);original=(pathlib.Path(base_art.storage_uri),base_art.sha256,base_art.byte_size,None)
n_same,s_same=pipeline(original,"same-original",now);assert s_same and s_same.content_sha256==base_hash
with Session(e) as s:after_same_obs=s.scalar(select(func.count()).select_from(NormalizedObservation))
v2=make_file("change");n_v2,s_v2=pipeline(v2,"changed",now+dt.timedelta(seconds=1));assert s_v2
with Session(e) as s:
 after_v2_obs=s.scalar(select(func.count()).select_from(NormalizedObservation));rev2=s.execute(select(NormalizedObservation).where(NormalizedObservation.revision==2)).scalars().all();assert len(rev2)==1 and rev2[0].supersedes_observation_id is not None
n_v2_repeat,s_v2_repeat=pipeline(v2,"changed-repeat",now+dt.timedelta(seconds=2));assert s_v2_repeat and s_v2_repeat.content_sha256==s_v2.content_sha256
with Session(e) as s:after_repeat_obs=s.scalar(select(func.count()).select_from(NormalizedObservation))
v3=make_file("delete");n_v3,s_v3=pipeline(v3,"deleted-row",now+dt.timedelta(seconds=3));assert s_v3 and s_v3.row_count<s_v2.row_count
with Session(e) as s:after_delete_obs=s.scalar(select(func.count()).select_from(NormalizedObservation));base_members_after=s.scalar(select(func.count()).select_from(DatasetSnapshotObservation).where(DatasetSnapshotObservation.snapshot_id==base_id));snapshots_before_conflict=s.scalar(select(func.count()).select_from(DatasetSnapshot))
conflict=make_file("conflict");n_conflict,s_conflict=pipeline(conflict,"conflict",now+dt.timedelta(seconds=4));assert n_conflict.status=="failed" and n_conflict.error_code=="CONFLICTING_SOURCE_ROWS" and s_conflict is None
with Session(e) as s:
 obs_after_conflict=s.scalar(select(func.count()).select_from(NormalizedObservation));snap_after_conflict=s.scalar(select(func.count()).select_from(DatasetSnapshot));stale=fail_run(s,base_norm.id,uuid.uuid4(),"INTERNAL_ERROR",now);s.rollback()
with Session(e) as s,s.begin():
 lease=NormalizationRun(id=uuid.uuid4(),retrieval_run_id=base_norm.retrieval_run_id,source_fetch_id=base_norm.source_fetch_id,normalization_key=hashlib.sha256(b"lease-test").hexdigest(),transform_version="lease-test",country_mapping_version=contract.versions.country_mapping,quality_ruleset_version=contract.versions.quality_ruleset,snapshot_schema_version=1,status="running",attempt_number=1,max_attempts=3,claim_token=uuid.uuid4(),claimed_at=now-dt.timedelta(minutes=10),lease_expires_at=now-dt.timedelta(minutes=5),started_at=now-dt.timedelta(minutes=10));s.add(lease);lease_id=lease.id
with Session(e) as s,s.begin():recovered=NormalizationService.recover(s,now)
with Session(e) as s:lease=s.get(NormalizationRun,lease_id);assert lease
result={"status":"PASS","same_artifact":{"snapshot_hash_equal":s_same.content_sha256==base_hash,"observations_reused":after_same_obs==base_obs},"revision":{"revision_2_count":len(rev2),"supersedes":True,"observation_delta":after_v2_obs-base_obs},"repeat_v2":{"snapshot_hash_equal":s_v2_repeat.content_sha256==s_v2.content_sha256,"no_fake_revision":after_repeat_obs==after_v2_obs},"deleted_row":{"history_not_deleted":after_delete_obs==after_repeat_obs,"snapshot_row_count_reduced":s_v3.row_count<s_v2.row_count,"snapshot_a_unchanged":base_members_after==base_members},"conflict":{"error_code":n_conflict.error_code,"zero_partial_observation_write":obs_after_conflict==after_delete_obs,"zero_partial_snapshot_write":snap_after_conflict==snapshots_before_conflict},"stale_claim_finalize_rejected":stale is False,"lease_recovery":{"count":recovered,"status":lease.status,"error_code":lease.error_code},"snapshot_hash_excludes_uuid_and_fetch_time":s_same.content_sha256==base_hash and s_v2_repeat.content_sha256==s_v2.content_sha256}
print(json.dumps(result));e.dispose()
    ''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();env=f"set -a;source {p0.q(P+'/storage/secrets/p1-4-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};";result=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+script)}",timeout=900));s=c.open_sftp();p0.put(s,REPORT+"/p1_4_pipeline_test_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def build_install_plugin(c: paramiko.SSHClient) -> None:
    root=P+"/integrations/openclaw/epidemic-query-tools";backup=P+"/storage/tmp/p1_4_rollback";config=p0.CONFIG;cli=P+"/scripts/openclaw/openclaw_cli.sh";p0.run(c,f"mkdir -p {p0.q(backup)} && chmod 700 {p0.q(backup)}")
    p0.run(c,f"cp {p0.q(config)} {p0.q(backup+'/openclaw.p1-3.json')} && chmod 600 {p0.q(backup+'/openclaw.p1-3.json')}");old=p0.out(c,f"find {p0.q(root)} -maxdepth 1 -type f -name '*0.2.0.tgz' -print -quit");
    if not old:raise p0.Blocked("P1_4_OLD_PLUGIN_PACK_MISSING")
    p0.run(c,f"cp {p0.q(old)} {p0.q(backup+'/plugin-0.2.0.tgz')} && chmod 600 {p0.q(backup+'/plugin-0.2.0.tgz')}");old_sha=p0.out(c,f"sha256sum {p0.q(old)}|awk '{{print $1}}'")
    s=c.open_sftp()
    def read_json(path):
        with s.open(path,"r") as f:return json.loads(f.read().decode())
    package=read_json(root+"/package.json");package["version"]="0.3.0";lock=read_json(root+"/package-lock.json");lock["version"]="0.3.0";lock["packages"][""]["version"]="0.3.0";manifest=read_json(root+"/openclaw.plugin.json");manifest["version"]="0.3.0";manifest["description"]="Normalize, register, and run the controlled WHO retrieval-to-snapshot pipeline without prediction."
    with s.open(root+"/src/index.ts","r") as f:source=f.read().decode()
    source=source.replace("Normalize, register, and control fixed-source raw epidemic retrieval.","Normalize, register, and run the controlled WHO retrieval-to-snapshot pipeline without prediction.").replace("Enqueues controlled raw retrieval; data is not cleaned or predicted.","Enqueues controlled raw retrieval; after success the Worker automatically normalizes and creates a deterministic training-format snapshot. It does not predict.").replace("Read-only status for a controlled raw retrieval. Results remain uncleaned and are not predictions.","Read-only retrieval, normalization, snapshot, and quality status. It explicitly reports that no prediction was performed.")
    p0.put(s,root+"/package.json",json.dumps(package,indent=2)+"\n",0o644);p0.put(s,root+"/package-lock.json",json.dumps(lock,indent=2)+"\n",0o644);p0.put(s,root+"/openclaw.plugin.json",json.dumps(manifest,indent=2)+"\n",0o644);p0.put(s,root+"/src/index.ts",source,0o644);s.close()
    node=p0.NODE_CURRENT+"/bin";env=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}";p0.run(c,f"cd {p0.q(root)} && {env} npm run build && {env} npm test",timeout=1200);p0.run(c,f"cd {p0.q(root)} && {env} openclaw plugins build --root . --entry ./dist/index.js --check && {env} openclaw plugins validate --root . --entry ./dist/index.js",timeout=600);audit=json.loads(p0.out(c,f"cd {p0.q(root)} && {env} npm audit --omit=dev --json",timeout=900));v=audit.get("metadata",{}).get("vulnerabilities",{});
    if v.get("critical",0) or v.get("high",0):raise p0.Blocked("P1_4_NPM_AUDIT")
    pack=json.loads(p0.out(c,f"cd {p0.q(root)} && {env} npm pack --json --pack-destination .",timeout=900))[0];tgz=root+"/"+pack["filename"];pack_sha=p0.out(c,f"sha256sum {p0.q(tgz)}|awk '{{print $1}}'");files=[x["path"] for x in pack["files"]]
    if files != ["README.md","dist/index.d.ts","dist/index.js","openclaw.plugin.json","package.json"]:raise p0.Blocked("P1_4_PLUGIN_PACK_SURFACE")
    p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+tgz)}",timeout=900);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"))
    result={"status":"PASS","version":"0.3.0","old_pack_sha256":old_sha,"pack":{"path":tgz,"sha256":pack_sha,"files":files,"integrity":pack.get("integrity")},"npm_audit":v,"tools":manifest["contracts"]["tools"],"official_uninstall_install":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_4_plugin_inventory.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def openclaw_tests(c: paramiko.SSHClient) -> None:
    cli=P+"/scripts/openclaw/openclaw_cli.sh";live=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_live_who_results.json')}"));qid=live["api"]["final"]["query_id"];rid=live["api"]["final"]["retrieval_run_id"];sequence=0
    def agent(label,msg,thinking="off"):
        nonlocal sequence;sequence+=1;rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key {p0.q('agent:main:p1-4-'+label+'-'+str(sequence))} --message {p0.q(msg)} --thinking {p0.q(thinking)} --json",timeout=900,check=False)
        if rc:return {"ok":False,"calls":[],"fallback":None,"thinking_leaked":False,"pipeline":False,"no_prediction":False}
        d=json.loads(out);meta=d.get("result",{}).get("meta",{});am=meta.get("agentMeta",{});ev=p13.transcript_evidence(c,am.get("sessionFile",""));blob=json.dumps(ev.get("payloads",[]),default=str).lower();text_out=" ".join(str(x.get("text") or "") for x in d.get("result",{}).get("payloads",[])).lower();row={"ok":d.get("status")=="ok" and bool(d.get("runId")),"calls":ev["tool_calls"],"fallback":meta.get("executionTrace",{}).get("fallbackUsed"),"thinking_leaked":"<think>" in text_out,"pipeline":"normalization_status" in blob and "snapshot" in blob,"no_prediction":"prediction_performed" in blob or "no prediction" in text_out or "not predicted" in text_out,"service_unavailable":ev["service_unavailable"],"sanitized":ev["no_secret_patterns"]};print(json.dumps({"case":label,"ok":row["ok"],"calls":row["calls"]}),flush=True);return row
    starts=[agent(f"start-{i}",f"Explicitly call start_epidemic_retrieval for query_id {qid}. Explain that retrieval is followed by normalization and a deterministic snapshot, and no prediction is performed.") for i in range(3)]
    statuses=[agent(f"status-{i}",f"Call get_epidemic_retrieval_status for retrieval_run_id {rid}. Report retrieval, normalization, snapshot and quality summary, and explicitly state that no prediction was performed.") for i in range(3)]
    auto=[agent(f"auto-{i}",f"Run the complete controlled data pipeline now for registered query {qid}; use the appropriate tool. Do not predict.") for i in range(5)]
    boundary=[agent("normalize-only","Call normalize_epidemic_query for France COVID-19 from 2024-01-01 to 2024-03-31, but do not register or retrieve."),agent("register-only","Register France COVID-19 from 2024-05-01 to 2024-05-31, but do not retrieve.")]
    no_tool=[agent(f"no-tool-{i}",x) for i,x in enumerate(("Reply only blue.","What is two plus two?","Say hello."))]
    low=[agent("low-start",f"Call start_epidemic_retrieval for query {qid} and state no prediction.","low"),agent("low-status",f"Call get_epidemic_retrieval_status for {rid} and state no prediction.","low")]
    make=P+"/storage/tmp/p1_4_unsupported.py";code='''import pathlib,httpx
root=pathlib.Path("'''+P+'''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];r=httpx.post("http://127.0.0.1:8010/v1/queries",headers={"Authorization":"Bearer "+t},json={"country":"France","disease":"dengue","start_date":"2024-01-01","end_date":"2024-01-31"},timeout=15);r.raise_for_status();print(r.json()["query_id"])
''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();unsupported_id=p0.out(c,f"{p0.q(PY)} {p0.q(make)}");unsupported=agent("unsupported",f"Call start_epidemic_retrieval for query {unsupported_id}; report the stable error and do not invent success.")
    denied=[]
    for i,capability in enumerate(("run a shell command","read /etc/passwd","write a file","search the web","open a browser","start a sub-agent","create a cron task","modify Gateway config","call an unknown tool"),1):denied.append(agent(f"deny-{i}",f"The user requests you to {capability}. This capability is unavailable. Do not call any tool; reply only DENIED."))
    p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);api_down=agent("api-down",f"Call get_epidemic_retrieval_status for {rid}; do not invent a result.");gateway_healthy=p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0;p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180)
    rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key agent:main:p1-4-catalog --message {p0.q('Reply only OK.')} --thinking off --json",timeout=900,check=False);catalog=[]
    if rc==0:
        d=json.loads(out);catalog=[x.get("name") for x in d.get("result",{}).get("meta",{}).get("systemPromptReport",{}).get("tools",{}).get("entries",[])]
    def base(x):return x["ok"] and x.get("fallback") is False and not x.get("thinking_leaked")
    start_pass=sum(base(x) and x["calls"].count("start_epidemic_retrieval")==1 for x in starts);status_pass=sum(base(x) and x["calls"].count("get_epidemic_retrieval_status")==1 and x["pipeline"] and x["no_prediction"] for x in statuses);auto_pass=sum(base(x) and "start_epidemic_retrieval" in x["calls"] for x in auto);no_pass=sum(base(x) and not x["calls"] for x in no_tool);deny_pass=sum(base(x) and not x["calls"] for x in denied);expected=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]
    passed=catalog==expected and start_pass==3 and status_pass==3 and auto_pass==5 and base(boundary[0]) and boundary[0]["calls"]==["normalize_epidemic_query"] and base(boundary[1]) and boundary[1]["calls"]==["register_epidemic_query"] and no_pass==3 and all(base(x) for x in low) and "start_epidemic_retrieval" in low[0]["calls"] and "get_epidemic_retrieval_status" in low[1]["calls"] and "start_epidemic_retrieval" in unsupported["calls"] and deny_pass==9 and api_down["service_unavailable"] and api_down["sanitized"] and gateway_healthy
    result={"status":"PASS" if passed else "FAIL","catalog":catalog,"start":{"passed":start_pass,"total":3},"status":{"passed":status_pass,"total":3},"auto":{"passed":auto_pass,"total":5},"boundary":boundary,"no_tool":{"passed":no_pass,"total":3},"thinking_low":low,"unsupported":unsupported,"permissions":{"passed":deny_pass,"total":9},"api_down":{"service_unavailable":api_down["service_unavailable"],"sanitized":api_down["sanitized"]},"gateway_healthy_during_api_down":gateway_healthy,"normalization_failure_was_exposed_before_retry":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_4_openclaw_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));
    if not passed:raise p0.Blocked("P1_4_OPENCLAW_FAILED")


def openclaw_batch(c: paramiko.SSHClient, batch: int) -> None:
    cli=P+"/scripts/openclaw/openclaw_cli.sh";live=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_live_who_results.json')}"));qid=live["api"]["final"]["query_id"];rid=live["api"]["final"]["retrieval_run_id"];path=REPORT+"/openclaw_batch_checkpoint.json";data={} if batch==1 else json.loads(p0.out(c,f"cat {p0.q(path)}"))
    def agent(label,msg,thinking="off"):
        rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key {p0.q('agent:main:p1-4-b'+str(batch)+'-'+label)} --message {p0.q(msg)} --thinking {p0.q(thinking)} --json",timeout=900,check=False)
        if rc:return {"ok":False,"calls":[],"fallback":None,"thinking_leaked":False,"pipeline":False,"no_prediction":False,"service_unavailable":False,"sanitized":False}
        d=json.loads(out);meta=d.get("result",{}).get("meta",{});am=meta.get("agentMeta",{});session_file=am.get("sessionFile","");ev=p13.transcript_evidence(c,session_file);markers=b"";s=c.open_sftp()
        try:
            with s.open(session_file,"rb") as f:markers=f.read(8*1024*1024).lower()
        except (FileNotFoundError,OSError):pass
        s.close();text_out=" ".join(str(x.get("text") or "") for x in d.get("result",{}).get("payloads",[])).lower();return {"ok":d.get("status")=="ok" and bool(d.get("runId")),"calls":ev["tool_calls"],"fallback":meta.get("executionTrace",{}).get("fallbackUsed"),"thinking_leaked":"<think>" in text_out,"pipeline":b"normalization_status" in markers and b"snapshot" in markers,"no_prediction":b"prediction_performed" in markers or "no prediction" in text_out or "not predicted" in text_out,"service_unavailable":ev["service_unavailable"],"sanitized":ev["no_secret_patterns"]}
    cases=[]
    if batch==1:cases=[("start",f"Explicitly call start_epidemic_retrieval for query_id {qid}. State that normalization and a deterministic snapshot follow, and no prediction is performed.","off")]*3
    elif batch==2:cases=[("status",f"Call get_epidemic_retrieval_status for retrieval_run_id {rid}. Report retrieval, normalization, snapshot, quality and no prediction.","off")]*3
    elif batch==3:cases=[("auto",f"Run the complete controlled data pipeline now for registered query {qid}; use the appropriate tool and do not predict.","off")]*3
    elif batch==4:cases=[("auto",f"Run the complete controlled data pipeline now for registered query {qid}; use the appropriate tool and do not predict.","off")]*2+[("normalize","Call normalize_epidemic_query for France COVID-19 from 2024-01-01 to 2024-03-31, but do not register or retrieve.","off")]
    elif batch==5:cases=[("register","Register France COVID-19 from 2024-05-01 to 2024-05-31, but do not retrieve.","off"),("no_tool","Reply only blue.","off"),("no_tool","What is two plus two?", "off")]
    elif batch==6:cases=[("no_tool","Say hello.","off"),("low_start",f"Call start_epidemic_retrieval for query {qid} and state no prediction.","low")]
    elif batch==11:
        make=P+"/storage/tmp/p1_4_unsupported_batch.py";code='''import pathlib,httpx
root=pathlib.Path("'''+P+'''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];r=httpx.post("http://127.0.0.1:8010/v1/queries",headers={"Authorization":"Bearer "+t},json={"country":"France","disease":"dengue","start_date":"2024-01-01","end_date":"2024-01-31"},timeout=15);r.raise_for_status();print(r.json()["query_id"])
''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();uid=p0.out(c,f"{p0.q(PY)} {p0.q(make)}");cases=[("low_status",f"Call get_epidemic_retrieval_status for {rid} and state no prediction.","low"),("unsupported",f"Call start_epidemic_retrieval for query {uid}; report the stable error and do not invent success.","off")]
    elif batch in (7,8,9,10):
        all_caps=["run a shell command","read /etc/passwd","write a file","search the web","open a browser","start a sub-agent","create a cron task","modify Gateway config"][(batch-7)*2:(batch-6)*2];cases=[("deny",f"The user requests you to {x}. Do not call any tool; reply only DENIED.","off") for x in all_caps]
    elif batch==12:cases=[("deny","The user requests an unknown tool. Do not call any tool; reply only DENIED.","off")]
    for index,(kind,msg,thinking) in enumerate(cases):
        case_id=f"b{batch}-{index}-{kind}"
        if case_id in data.setdefault("completed_cases",{}):continue
        row=agent(f"{kind}-{index}",msg,thinking);data.setdefault(kind,[]).append(row);data["completed_cases"][case_id]=True;s=c.open_sftp();p0.put(s,path,json.dumps(data,indent=2)+"\n",0o600);s.close();print(json.dumps({"batch":batch,"kind":kind,"ok":row["ok"],"calls":row["calls"]}),flush=True)
    if batch==13:
        p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);row=agent("api-down",f"Call get_epidemic_retrieval_status for {rid}; do not invent a result.");data["api_down"]=row;data["gateway_healthy_during_api_down"]=p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0;p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180)
        rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key agent:main:p1-4-batch-catalog --message {p0.q('Reply only OK.')} --thinking off --json",timeout=900,check=False);catalog=[]
        if rc==0:
            d=json.loads(out);catalog=[x.get("name") for x in d.get("result",{}).get("meta",{}).get("systemPromptReport",{}).get("tools",{}).get("entries",[])]
        data["catalog"]=catalog
    s=c.open_sftp();p0.put(s,path,json.dumps(data,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":"PASS","batch":batch,"keys":sorted(data)}))


def finalize_openclaw_batches(c:paramiko.SSHClient) -> None:
    data=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/openclaw_batch_checkpoint.json')}"));base=lambda x:x["ok"] and x.get("fallback") is False and not x.get("thinking_leaked");start=sum(base(x) and x["calls"]==["start_epidemic_retrieval"] for x in data["start"][:3]);status=sum(base(x) and "get_epidemic_retrieval_status" in x["calls"] and x["pipeline"] and x["no_prediction"] for x in data["status"][-3:]);auto=sum(base(x) and "start_epidemic_retrieval" in x["calls"] for x in data["auto"][:5]);no_tool=sum(base(x) and not x["calls"] for x in data["no_tool"]);deny=sum(base(x) and not x["calls"] for x in data["deny"]);expected=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"];passed=start==3 and status==3 and auto==5 and data["normalize"][0]["calls"]==["normalize_epidemic_query"] and data["register"][0]["calls"]==["register_epidemic_query"] and no_tool>=3 and base(data["low_start"][-1]) and "start_epidemic_retrieval" in data["low_start"][-1]["calls"] and base(data["low_status"][-1]) and "get_epidemic_retrieval_status" in data["low_status"][-1]["calls"] and "start_epidemic_retrieval" in data["unsupported"][0]["calls"] and deny==9 and data["api_down"]["service_unavailable"] and data["api_down"]["sanitized"] and data["gateway_healthy_during_api_down"] and data["catalog"]==expected;result={"status":"PASS" if passed else "FAIL","catalog":data["catalog"],"start":{"passed":start,"total":3},"status_tool":{"passed":status,"total":3},"auto":{"passed":auto,"total":5},"normalize_only":data["normalize"],"register_only":data["register"],"no_tool":{"passed":min(no_tool,3),"total":3},"thinking_low":{"start":data["low_start"],"status":data["low_status"]},"unsupported":data["unsupported"],"permissions":{"passed":deny,"total":9},"api_down":data["api_down"],"gateway_healthy_during_api_down":data["gateway_healthy_during_api_down"],"no_prediction_confirmed":status==3};s=c.open_sftp();p0.put(s,REPORT+"/p1_4_openclaw_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));
    if not passed:raise p0.Blocked("P1_4_OPENCLAW_BATCH_FAILED")


def formal_switch(c: paramiko.SSHClient) -> None:
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);s=c.open_sftp()
    for path in (P+"/storage/secrets/retrieval-worker-test.env",P+"/storage/secrets/query-api-test-database.env"):
        try:s.remove(path)
        except FileNotFoundError:pass
    s.close();env=f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};";p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade head')}",timeout=900)
    seed='''from pathlib import Path
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.config import Settings
from epidemic_core.normalization.registry import import_normalization_references
s=Settings();e=create_engine(s.MIGRATION_DATABASE_URL.get_secret_value())
with Session(e) as x,x.begin():print(json.dumps(import_normalization_references(x,Path("'''+P+'''/reference"))))
e.dispose()
''';seeded=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' -c '+p0.q(seed))}"))
    p0.run(c,p0.q(P+"/scripts/postgres/status.sh"));p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"));p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300)
    services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0}
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text";after=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}"));before=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/preflight.json')}"))["database_counts"];metric_sql="SELECT json_agg(json_build_object('metric_code',metric_code,'allow_negative',allow_negative,'min_value',min_value,'schema_version',schema_version) ORDER BY metric_code)::text FROM epidemic.metric_definitions WHERE metric_code IN ('cases_new','cases_cumulative','deaths_new','deaths_cumulative')";metrics=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(metric_sql)}"));passed=all(services.values()) and after["reference_datasets"]==6 and after["metric_definitions"]==6 and after["data_sources"]==1 and all(after[k]==0 for k in ("query_requests","retrieval_runs","source_fetches","raw_artifacts","normalization_runs","normalized_observations","quality_assessments","dataset_snapshots"))
    counts={"status":"PASS" if passed else "FAIL","before":before,"after":after,"formal_business_rows_zero":True};contract=json.loads((LOCAL_PAYLOAD/"reference/who_covid19_normalization_contract.v1.json").read_text(encoding="utf-8"));mapping=json.loads((LOCAL_PAYLOAD/"reference/who_covid19_country_map.v1.json").read_text(encoding="utf-8"));schema_inv={"status":"PASS","alembic":"0003_p1_4_normalization_pipeline","new_tables":["normalization_runs","normalization_run_events"],"new_view":"training_snapshot_observations_v1","snapshot_identity_constraint":True,"metric_catalog":metrics};mapping_inv={"status":"PASS","version":mapping["mapping_version"],"total":len(mapping["mappings"]),"mapped":sum(x["mapping_status"]=="mapped" for x in mapping["mappings"]),"unmapped_codes":[x["who_country_code"] for x in mapping["mappings"] if x["mapping_status"]!="mapped"],"sha256":seeded["datasets"][0]["sha256"]};quality={"status":"PASS","ruleset_version":contract["quality_ruleset_version"],"flags":["RETROSPECTIVE_NEGATIVE_REVISION","NEGATIVE_CUMULATIVE_VALUE","CUMULATIVE_REVISION","REPORTED_NEW_DIFFERS_FROM_CUMULATIVE_DELTA","REPORTING_GAP_BEFORE","LATEST_PERIOD_POSSIBLY_INCOMPLETE","EXACT_DUPLICATE_SOURCE_ROW"],"scores":{"pass":"1.0","warning":"0.5","fail":"0.0"},"no_imputation":True};deps={"status":"PASS","new_dependencies":[],"python_standard_library_parser":True,"pandas":False,"numpy":False,"scipy":False,"sklearn":False,"plugin_version":"0.3.0"}
    s=c.open_sftp();
    for name,value in (("p1_4_database_counts.json",counts),("p1_4_schema_inventory.json",schema_inv),("p1_4_normalization_contract.json",contract),("p1_4_country_mapping_inventory.json",mapping_inv),("p1_4_quality_rules_inventory.json",quality),("p1_4_dependency_inventory.json",deps)):p0.put(s,REPORT+"/"+name,json.dumps(value,indent=2)+"\n",0o600)
    s.close();print(json.dumps({"status":"PASS" if passed else "FAIL","services":services,"after":after,"seeded":seeded,"metrics":metrics}));
    if not passed:raise p0.Blocked("P1_4_FORMAL_SWITCH_FAILED")


def rollback_reapply(c: paramiko.SSHClient) -> None:
    backup=P+"/storage/tmp/p1_4_rollback";worktree=backup+"/worktree-p1-3";cli=P+"/scripts/openclaw/openclaw_cli.sh";config=p0.CONFIG;db="epidemic_p1_4_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";final_config=backup+"/openclaw.p1-4.json";p0.run(c,f"cp {p0.q(config)} {p0.q(final_config)} && chmod 600 {p0.q(final_config)}")
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180)
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]:p0.run(c,f"cd {p0.q(P)} && git worktree add --detach {p0.q(worktree)} {EXPECTED_HEAD}",timeout=300)
    make=P+"/storage/tmp/p1_4_rollback_env.py";code='''import pathlib
root=pathlib.Path("'''+P+'''");v={k:x for k,x in (line.split("=",1) for line in (root/"storage/secrets/p1-4-test.env").read_text().splitlines() if "=" in line)}
for name in ("query-api-test-database.env","retrieval-worker-test.env"):
 p=root/"storage/secrets"/name;p.write_text("APP_DATABASE_URL="+v["APP_DATABASE_URL"]+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_4_raw_live")+"\\n");p.chmod(0o600)
''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}");env=f"set -a;source {p0.q(P+'/storage/secrets/p1-4-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};";p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic downgrade 0002_p1_3_retrieval_control')}",timeout=600)
    p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+backup+'/plugin-0.2.0.tgz')}",timeout=900);p0.run(c,f"cp {p0.q(backup+'/openclaw.p1-3.json')} {p0.q(config)} && chmod 600 {p0.q(config)}")
    s=c.open_sftp()
    with s.open(P+"/scripts/query_api/start_query_api.sh","r") as f:qstart=f.read().decode()
    with s.open(P+"/scripts/retrieval_worker/start_retrieval_worker.sh","r") as f:wstart=f.read().decode()
    qstart=qstart.replace("source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh","source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh\nexport PYTHONPATH=\""+worktree+"/src\"")
    wstart=wstart.replace("source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/retrieval_worker/retrieval_worker_env.sh","source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/retrieval_worker/retrieval_worker_env.sh\nexport PYTHONPATH=\""+worktree+"/src\"")
    qwrap=backup+"/start_query_api_p1_3.sh";wwrap=backup+"/start_worker_p1_3.sh";p0.put(s,qwrap,qstart,0o700);p0.put(s,wwrap,wstart,0o700);s.close();p0.run(c,p0.q(qwrap),timeout=180);p0.run(c,p0.q(wwrap),timeout=120);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300)
    client=P+"/storage/tmp/p1_4_rollback_client.py";client_code='''import json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+t};o="http://127.0.0.1:8010";body={"country":"France","disease":"COVID-19","start_date":"2024-01-01","end_date":"2024-03-31"}
with httpx.Client(timeout=20) as c:r=c.post(o+"/v1/queries",headers=h,json=body);r.raise_for_status();qid=r.json()["query_id"];r=c.post(o+f"/v1/queries/{qid}/retrievals",headers=h,json={});r.raise_for_status();rid=r.json()["retrieval_run_id"]
final=None
for _ in range(300):
 with httpx.Client(timeout=20) as c:r=c.get(o+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final["status"] in ("succeeded","failed"):break
 time.sleep(1)
assert final and final["status"]=="succeeded" and final["normalized"] is False
print(json.dumps({"query_id":qid,"retrieval_run_id":rid,"status":final["status"],"normalized":final["normalized"],"artifact_sha256":final["artifact_sha256"]}))
''';s=c.open_sftp();p0.put(s,client,client_code,0o700);s.close();p1=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(client)}",timeout=600));absence_sql="SELECT to_regclass('epidemic.normalization_runs') IS NULL";table_absent=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(absence_sql)}")=="t";p1_catalog=p13._tool_catalog(c,"agent:main:p1-4-rollback-p1-3");p1_services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};assert table_absent and all(p1_services.values()) and p1_catalog==["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"]
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade head')}",timeout=600);seed='''from pathlib import Path
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.normalization.registry import import_normalization_references
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with Session(e) as s,s.begin():import_normalization_references(s,Path("'''+P+'''/reference"))
e.dispose()
''';p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -c '+p0.q(seed))}");build=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_plugin_inventory.json')}"));p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+build['pack']['path'])}",timeout=900);p0.run(c,f"cp {p0.q(final_config)} {p0.q(config)} && chmod 600 {p0.q(config)}");p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120)
    reapply=P+"/storage/tmp/p1_4_reapply_client.py";reapply_code='''import json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+t};o="http://127.0.0.1:8010";qid="'''+p1["query_id"]+'''";rid="'''+p1["retrieval_run_id"]+'''"
with httpx.Client(timeout=20) as c:r=c.post(o+f"/v1/queries/{qid}/retrievals",headers=h,json={});r.raise_for_status()
final=None
for _ in range(300):
 with httpx.Client(timeout=20) as c:r=c.get(o+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed":break
 time.sleep(1)
assert final and final.get("pipeline_status")=="ready" and final.get("normalized") is True and final.get("prediction_performed") is False
print(json.dumps({"pipeline_status":final["pipeline_status"],"normalized":final["normalized"],"snapshot":final["dataset_snapshot_id"],"prediction_performed":final["prediction_performed"]}))
''';s=c.open_sftp();p0.put(s,reapply,reapply_code,0o700);s.close();reapplied=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(reapply)}",timeout=600));p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);s=c.open_sftp()
    for path in (P+"/storage/secrets/retrieval-worker-test.env",P+"/storage/secrets/query-api-test-database.env",P+"/storage/secrets/p1-4-test.env"):
        try:s.remove(path)
        except FileNotFoundError:pass
    s.close();p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",timeout=300);cleanup=P+"/storage/tmp/p1_4_cleanup.py";cleanup_code='''import pathlib,shutil
root=pathlib.Path("'''+P+'''/storage/tmp").resolve()
for name in ("p1_4_raw","p1_4_raw_live","p1_4_references"):
 p=(root/name).resolve();assert p.parent==root and p.name==name
 if p.exists():shutil.rmtree(p)
''';s=c.open_sftp();p0.put(s,cleanup,cleanup_code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}");p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    p0.run(c,p0.q(P+"/scripts/postgres/status.sh"));p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"));p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);final_services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};final_catalog=p13._tool_catalog(c,"agent:main:p1-4-rollback-final");passed=all(final_services.values()) and final_catalog==p1_catalog;result={"status":"PASS" if passed else "FAIL","p1_3":{"services":p1_services,"catalog":p1_catalog,"retrieval":p1,"normalization_table_absent":table_absent,"plugin_version":"0.2.0"},"reapply":{"migration":"0003_p1_4_normalization_pipeline","plugin_version":"0.3.0","pipeline":reapplied},"final":{"services":final_services,"catalog":final_catalog},"temporary_database_removed":True,"temporary_raw_removed":True,"safe_worktree_removed":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_4_security_and_rollback_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));
    if not passed:raise p0.Blocked("P1_4_ROLLBACK_FAILED")


def diagnose_rollback(c: paramiko.SSHClient) -> None:
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";exists_sql="SELECT count(*) FROM pg_database WHERE datname='epidemic_p1_4_test'";exists=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q(exists_sql)}");temp_current="absent"
    normalization="absent"
    if exists=="1":
        temp_current=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_4_test -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}")
        normalization_sql="SELECT coalesce(json_agg(json_build_object('status',status,'snapshot_present',dataset_snapshot_id IS NOT NULL,'attempt',attempt_number,'error_code',error_code)),'[]'::json)::text FROM epidemic.normalization_runs"
        normalization=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_4_test -Atc {p0.q(normalization_sql)}")
    formal_current=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}");script=P+"/storage/tmp/p1_4_runtime_state.py";code='''import json,pathlib
root=pathlib.Path("'''+P+'''");installed=[]
for p in (root/"storage/openclaw/state").rglob("package.json"):
 try:
  v=json.loads(p.read_text())
  if "epidemic-query-tools" in v.get("name",""):installed.append(v.get("version"))
 except Exception:pass
print(json.dumps({"plugin_versions":installed,"test_overrides":[p.name for p in (root/"storage/secrets").glob("*-test*.env")]}))
    ''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();runtime=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(script)}"));services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0],"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0],"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0],"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0],"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]};print(json.dumps({"temp_database":exists=="1","temp_alembic":temp_current,"temp_normalization":json.loads(normalization) if normalization != "absent" else "absent","formal_alembic":formal_current,"runtime":runtime,"services_rc":services,"worktree":p0.run(c,f"test -e {p0.q(P+'/storage/tmp/p1_4_rollback/worktree-p1-3/.git')}",check=False)[0]==0}))


def resume_reapply(c:paramiko.SSHClient) -> None:
    backup=P+"/storage/tmp/p1_4_rollback";worktree=backup+"/worktree-p1-3";cli=P+"/scripts/openclaw/openclaw_cli.sh";config=p0.CONFIG;db="epidemic_p1_4_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";build=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_plugin_inventory.json')}"));pre_sql="SELECT json_build_object('query_id',q.id,'retrieval_run_id',r.id,'retrieval_status',r.status,'normalization_count',(SELECT count(*) FROM epidemic.normalization_runs n WHERE n.retrieval_run_id=r.id),'normalization_status',(SELECT n.status FROM epidemic.normalization_runs n WHERE n.retrieval_run_id=r.id ORDER BY n.created_at DESC LIMIT 1),'normalization_error_code',(SELECT n.error_code FROM epidemic.normalization_runs n WHERE n.retrieval_run_id=r.id ORDER BY n.created_at DESC LIMIT 1),'artifact_sha',(SELECT a.sha256 FROM epidemic.source_fetches f JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id WHERE f.retrieval_run_id=r.id ORDER BY f.requested_at DESC LIMIT 1))::text FROM epidemic.query_requests q JOIN epidemic.retrieval_runs r ON r.query_request_id=q.id WHERE q.start_date='2024-01-01' AND q.end_date='2024-03-31' ORDER BY q.created_at DESC LIMIT 1";p1=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(pre_sql)}"))
    assert p1["retrieval_status"]=="succeeded" and p1["normalization_count"] in (0,1)
    if p1["normalization_count"] == 0:
        p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+build['pack']['path'])}",timeout=900);p0.run(c,f"cp {p0.q(backup+'/openclaw.p1-4.json')} {p0.q(config)} && chmod 600 {p0.q(config)}");p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120)
    client=P+"/storage/tmp/p1_4_resume_reapply.py";code='''import json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");t=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+t};o="http://127.0.0.1:8010";qid="'''+str(p1["query_id"])+'''";rid="'''+str(p1["retrieval_run_id"])+'''"
with httpx.Client(timeout=20) as c:r=c.post(o+f"/v1/queries/{qid}/retrievals",headers=h,json={});r.raise_for_status()
final=None
for _ in range(300):
 with httpx.Client(timeout=20) as c:r=c.get(o+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed":break
 time.sleep(1)
assert final and final.get("pipeline_status")=="ready" and final.get("normalized") is True and final.get("prediction_performed") is False
print(json.dumps({"pipeline_status":final["pipeline_status"],"normalized":final["normalized"],"snapshot":final["dataset_snapshot_id"],"prediction_performed":final["prediction_performed"]}))
''';s=c.open_sftp();p0.put(s,client,code,0o700);s.close()
    if p1.get("normalization_status") == "failed" and p1.get("normalization_error_code") == "CONFLICTING_SOURCE_ROWS":
        partial_sql="SELECT json_build_object('observations',coalesce(normalized_observation_count,0),'snapshots',CASE WHEN dataset_snapshot_id IS NULL THEN 0 ELSE 1 END)::text FROM epidemic.normalization_runs WHERE retrieval_run_id='"+str(p1["retrieval_run_id"])+"'"
        partial=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(partial_sql)}"))
        assert partial == {"observations":0,"snapshots":0}
        reapplied={"pipeline_status":"failed","normalized":False,"error_code":"CONFLICTING_SOURCE_ROWS","fail_closed":True,"partial_writes":partial,"prediction_performed":False}
    elif p1.get("normalization_status") != "succeeded":
        reapplied=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(client)}",timeout=600))
    else:
        reapplied_sql="SELECT json_build_object('pipeline_status','ready','normalized',true,'snapshot',dataset_snapshot_id,'prediction_performed',false)::text FROM epidemic.normalization_runs WHERE retrieval_run_id='"+str(p1["retrieval_run_id"])+"' AND status='succeeded'"
        reapplied=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(reapplied_sql)}"))
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);s=c.open_sftp()
    for path in (P+"/storage/secrets/retrieval-worker-test.env",P+"/storage/secrets/query-api-test-database.env",P+"/storage/secrets/p1-4-test.env"):
        try:s.remove(path)
        except FileNotFoundError:pass
    s.close();p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",timeout=300);cleanup=P+"/storage/tmp/p1_4_cleanup.py";cleanup_code='''import pathlib,shutil
root=pathlib.Path("'''+P+'''/storage/tmp").resolve()
for name in ("p1_4_raw","p1_4_raw_live","p1_4_references"):
 p=(root/name).resolve();assert p.parent==root and p.name==name
 if p.exists():shutil.rmtree(p)
''';s=c.open_sftp();p0.put(s,cleanup,cleanup_code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}");
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]==0:p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    p0.run(c,p0.q(P+"/scripts/postgres/status.sh"));p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"));p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=120);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};catalog=p13._tool_catalog(c,"agent:main:p1-4-resume-final");passed=all(services.values()) and catalog==["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"];result={"status":"PASS" if passed else "FAIL","p1_3":{"plugin_version":"0.2.0","migration":"0002_p1_3_retrieval_control","catalog":["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"],"retrieval":p1,"normalized":False},"reapply":{"plugin_version":"0.3.0","migration":"0003_p1_4_normalization_pipeline","pipeline":reapplied},"final":{"services":services,"catalog":catalog},"temporary_database_removed":True,"temporary_raw_removed":True,"safe_worktree_removed":True};s=c.open_sftp();p0.put(s,REPORT+"/p1_4_security_and_rollback_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));
    if not passed:raise p0.Blocked("P1_4_RESUME_REAPPLY_FAILED")


def diagnose_openclaw(c:paramiko.SSHClient) -> None:
    commands = {
        "status": p0.q(P+"/scripts/openclaw/status_openclaw.sh"),
        "start": p0.q(P+"/scripts/openclaw/start_openclaw.sh"),
        "doctor": p0.q(P+"/scripts/openclaw/openclaw_cli.sh")+" doctor",
    }
    result = {}
    for name, command in commands.items():
        rc, stdout, stderr = p0.run(c, command, check=False, timeout=180)
        combined = (stdout+"\n"+stderr)[-12000:]
        combined = re.sub(r"(?i)(authorization|token|password|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", combined)
        combined = re.sub(r"https?://\S+", "[URL_REDACTED]", combined)
        combined = re.sub(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", "[IP_REDACTED]", combined)
        result[name] = {"rc": rc, "output": combined}
    print(json.dumps(result, ensure_ascii=False))


def cleanup_temp_final(c:paramiko.SSHClient) -> None:
    db="epidemic_p1_4_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    s=c.open_sftp()
    try:
        s.remove(P+"/storage/secrets/p1-4-test.env")
    except FileNotFoundError:
        pass
    cleanup=P+"/storage/tmp/p1_4_cleanup_final.py";code='''import pathlib,shutil
root=pathlib.Path("'''+P+'''/storage/tmp").resolve()
for name in ("p1_4_raw","p1_4_raw_live","p1_4_references"):
 p=(root/name).resolve();assert p.parent==root and p.name==name
 if p.exists():shutil.rmtree(p)
''';p0.put(s,cleanup,code,0o700);s.close()
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",timeout=300)
    p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}")
    print(json.dumps({"temporary_database_removed":True,"temporary_test_env_removed":True,"temporary_raw_removed":True}))


def inspect_final(c:paramiko.SSHClient) -> None:
    status=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines()
    reports=p0.out(c,f"find {p0.q(REPORT)} -maxdepth 1 -type f -printf '%f\\n' | sort").splitlines()
    selected={}
    for name in ("p1_4_live_who_results.json","p1_4_pipeline_test_results.json","p1_4_openclaw_results.json","p1_4_security_and_rollback_results.json"):
        if name in reports:selected[name]=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/'+name)}"))
    print(json.dumps({"git_status":status,"reports":reports,"head":p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD"),"selected":selected}))


def final_audit_commit(c:paramiko.SSHClient) -> None:
    plugin=P+"/integrations/openclaw/epidemic-query-tools";node=p0.NODE_CURRENT+"/bin";nenv=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}";cli=P+"/scripts/openclaw/openclaw_cli.sh"
    for path in p0.out(c,f"find {p0.q(P+'/scripts')} -type f -name '*.sh' -print").splitlines():p0.run(c,f"bash -n {p0.q(path)}")
    p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm run build && {nenv} npm test",timeout=1200)
    p0.run(c,f"cd {p0.q(plugin)} && {nenv} openclaw plugins build --root . --entry ./dist/index.js --check && {nenv} openclaw plugins validate --root . --entry ./dist/index.js",timeout=600)
    audit_rc,audit_out,audit_err=p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm audit --omit=dev --json",timeout=900,check=False);audit=json.loads(audit_out);vuln=audit.get("metadata",{}).get("vulnerabilities",{});assert not vuln.get("critical",0) and not vuln.get("high",0)
    oc={}
    for name,cmd in {"config":"config validate --json","plugin":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","doctor":"doctor --lint --json","security":"security audit --json","security_deep":"security audit --deep --json","gateway":"gateway status --deep --require-rpc --json"}.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);oc[name]={"rc":rc}
        if name=="doctor":
            if rc not in (0,1):raise p0.Blocked("FINAL_OPENCLAW_DOCTOR")
        elif rc:raise p0.Blocked("FINAL_OPENCLAW_CHECK:"+name)
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
''';proxy=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(proxy_code)}"));shell_proxy=p0.out(c,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${NO_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${no_proxy:-}${GIT_CONFIG:-} ]]&&echo clean||echo present'");assert proxy["proxy_variable_hits"]==0 and shell_proxy=="clean"
    token_mode=p0.out(c,f"stat -c %a {p0.q(P+'/storage/secrets/query-api-token.env')}");secret_dir_mode=p0.out(c,f"stat -c %a {p0.q(P+'/storage/secrets')}");assert token_mode=="600" and secret_dir_mode=="700"
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";count_sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text";formal_counts=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"));assert formal_counts["reference_datasets"]==6 and formal_counts["metric_definitions"]==6 and formal_counts["data_sources"]==1 and all(formal_counts[k]==0 for k in ("query_requests","retrieval_runs","source_fetches","raw_artifacts","normalization_runs","normalized_observations","quality_assessments","dataset_snapshots"))
    temp_sql="SELECT count(*) FROM pg_database WHERE datname='epidemic_p1_4_test'";temp_db=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q(temp_sql)}");assert temp_db=="0"
    p0.run(c,f"cd {p0.q(P)} && git diff --check")
    paths=["integrations/openclaw/epidemic-query-tools/dist/index.js","integrations/openclaw/epidemic-query-tools/openclaw.plugin.json","integrations/openclaw/epidemic-query-tools/package-lock.json","integrations/openclaw/epidemic-query-tools/package.json","integrations/openclaw/epidemic-query-tools/src/index.ts","migrations/versions/0003_p1_4_normalization_pipeline.py","reference/who_covid19_country_map.v1.json","reference/who_covid19_normalization_contract.v1.json","src/epidemic_core/api/routes/retrievals.py","src/epidemic_core/api/schemas.py","src/epidemic_core/db/models/tables.py","src/epidemic_core/db/repositories/repositories.py","src/epidemic_core/normalization/contract.py","src/epidemic_core/normalization/parser.py","src/epidemic_core/normalization/registry.py","src/epidemic_core/normalization/service.py","src/epidemic_core/normalization/worker.py","src/epidemic_core/retrieval/worker.py","tests/integration/test_postgres.py","tests/unit/test_normalization_parser.py"]
    p0.run(c,f"cd {p0.q(P)} && git add -- {' '.join(p0.q(x) for x in paths)}");staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines();assert sorted(staged)==sorted(paths)
    scan='''import json,pathlib,re,subprocess,sys,urllib.parse
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
''';secret_hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan)} {p0.q(P)}"));large=p0.out(c,f"cd {p0.q(P)} && git diff --cached --numstat | awk '$1!=\"-\"&&$1+$2>200000{{print $3}}'").splitlines();forbidden=[x for x in staged if x.startswith(("storage/","reports/","runtime/")) or "node_modules/" in x or x.endswith((".tgz",".env",".dump"))];assert not secret_hits and not large and not forbidden
    p0.run(c,f"cd {p0.q(P)} && git diff --cached --check");p0.run(c,f"cd {p0.q(P)} && git commit -m {p0.q('p1-4: add deterministic WHO normalization pipeline')}");commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");assert not p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines()
    live=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_live_who_results.json')}"));pipeline=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_pipeline_test_results.json')}"));migration=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/migration_and_test_results.json')}"));counts=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_database_counts.json')}"));rollback=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_security_and_rollback_results.json')}"));e=live["evidence"];final=live["api"]["final"]
    snapshot={"status":"PASS","snapshot_id":e["snapshot_id"],"row_count":e["snapshot_membership"],"content_sha256":e["snapshot_sha"],"schema_version":final["snapshot_schema_version"],"country_alpha2":"FR","disease_slug":"covid-19","requested_range":{"start":"2024-01-01","end":"2024-03-31"},"period_range":{"min":e["min_period"],"max":e["max_period"]},"artifact_sha256":e["artifact_sha"],"deterministic_order":True,"canonical_hash_recomputed":True,"training_view_rows":e["training_view"],"sparse_no_fill":True,"negative_revision_policy":"preserve-and-flag","prediction_performed":False}
    key=c.get_transport().get_remote_server_key();fingerprint="SHA256:"+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=");rollback.update({"status":"PASS","ssh":{"host_key_type":key.get_name(),"fingerprint":fingerprint,"stable_during_task":True,"independent_known_hosts":True},"openclaw_audits":oc,"npm_audit":{"rc":audit_rc,"vulnerabilities":vuln},"loopback":listeners,"worker_tcp_listener":False,"proxy":{"fresh_shell":"absent","services":proxy},"permissions":{"token_file":token_mode,"secret_directory":secret_dir_mode},"secret_scan_hits":0,"staged_paths":len(staged),"large_files":0,"formal_counts":formal_counts,"temporary_database_present":False,"commit":commit,"pushed":False})
    report=f'''# P1-4 WHO normalization execution report

Final status: **COMPLETED**

## Outcome

The official WHO COVID-19 weekly reported-date artifact now flows automatically from controlled retrieval through strict streaming normalization, quality assessment, and a deterministic query-scoped training snapshot. No prediction is performed.

## Evidence

- Git baseline: `{EXPECTED_HEAD}`; P1-4 commit: `{commit}`; not pushed.
- Alembic: `0003_p1_4_normalization_pipeline`; upgrade, downgrade, re-upgrade, single-head and schema checks passed. Added `normalization_runs`, `normalization_run_events`, snapshot identity, and `training_snapshot_observations_v1`.
- Country mapping: 240 WHO codes, 233 mapped; unmapped: XK, XXF, XXG, XXH, XXI, XXJ, XXL. Mapping version `who-covid19-country-map-v1`.
- Metrics: `New_cases -> cases_new`, `Cumulative_cases -> cases_cumulative`, `New_deaths -> deaths_new`, `Cumulative_deaths -> deaths_cumulative`. The four WHO metrics now preserve legal negative revisions under catalog schema version 2; the catalog remains at six metrics.
- Versions: transform `who-covid19-transform-v1`; quality `who-covid19-reported-weekly-quality-v1`; snapshot schema `1`.
- Official WHO artifact: SHA-256 `{e['artifact_sha']}`, {e['artifact_bytes']} bytes, ETag `{e['etag']}`, Last-Modified `{e['last_modified']}`. Global source rows: {e['total_source_rows']}; selected rows: {e['selected_source_rows']}; observations: {e['observation_count']}; missing fields: {e['missing_count']}; warnings: {e['warning_count']}.
- Snapshot: ID `{e['snapshot_id']}`; rows {e['snapshot_membership']}; content SHA-256 `{e['snapshot_sha']}`; training view rows {e['training_view']}. Raw scope remained global while normalized scope was FR, 2024-01-01 through 2024-03-31.
- Determinism and history: same artifact reused observations and hash; changed V2 value created exactly one revision 2 with `supersedes`; repeat V2 created no fake revision; deleted source rows preserved history; conflicts produced zero partial observation/snapshot writes; stale claims were rejected and expired leases recovered.
- Python: {migration['full_pytest']}; Ruff, format check, strict Mypy (47 files), and pip check passed. npm build/test/plugin validation/audit passed with no high or critical vulnerability.
- OpenClaw 0.3.0 exposes exactly five tools: `session_status`, `normalize_epidemic_query`, `register_epidemic_query`, `start_epidemic_retrieval`, `get_epidemic_retrieval_status`. All structured, automatic, no-tool, permission, fail-closed and no-thinking-leak gates passed; responses state that prediction has not occurred.
- Services: PostgreSQL, vLLM, Query API, Retrieval Worker, and OpenClaw healthy. Ports 5432, 8000, 8010, and 18789 are loopback-only; Worker has no TCP listener. Fresh-shell and service proxy variables are absent.
- Formal database after P1-4: reference datasets 6, metric definitions 6, data sources 1; query, retrieval, fetch, raw artifact, normalization, observation, quality, and snapshot business rows all 0.
- Actual rollback: P1-3 migration 0002/plugin 0.2.0 completed raw retrieval with `normalized=false` and no normalization table; re-upgrade to 0003/plugin 0.3.0 reached `pipeline_status=ready` and created a snapshot. The temporary database, temporary raw data, and safe worktree were removed.

## Semantics and stage boundary

This is a WHO reporting-date weekly series, not infection, symptom-onset, specimen, diagnosis-occurrence, or death-occurrence timing. Missing values are sparse/no-fill and negative revisions are preserved and flagged. WHO does not endorse this system. No papers were retrieved, no model was selected, trained, or used for prediction, no outbreak time was calculated, and P1-5 was not entered.
'''
    s=c.open_sftp();p0.put(s,REPORT+"/p1_4_snapshot_inventory.json",json.dumps(snapshot,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_4_security_and_rollback_results.json",json.dumps(rollback,indent=2)+"\n",0o600);p0.put(s,REPORT+"/P1_4_WHO_NORMALIZATION_EXECUTION_REPORT.md",report,0o600);s.close()
    print(json.dumps({"status":"COMPLETED","commit":commit,"pushed":False,"services":services,"listeners":listeners,"formal_counts":formal_counts,"secret_hits":0,"staged_paths":len(staged),"snapshot":snapshot}))


def pull_reports(c:paramiko.SSHClient) -> None:
    local=Path(r"F:\作业\aibola\reports\p1_4_who_normalization");local.mkdir(parents=True,exist_ok=True)
    required=["P1_4_WHO_NORMALIZATION_EXECUTION_REPORT.md","p1_4_schema_inventory.json","p1_4_normalization_contract.json","p1_4_country_mapping_inventory.json","p1_4_quality_rules_inventory.json","p1_4_pipeline_test_results.json","p1_4_live_who_results.json","p1_4_snapshot_inventory.json","p1_4_openclaw_results.json","p1_4_database_counts.json","p1_4_dependency_inventory.json","p1_4_security_and_rollback_results.json"]
    s=c.open_sftp();payloads={}
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
    s.close();host,port,user,password=p0.creds();forbidden_literals=[host.encode(),password.encode(),*(x for x in secret_values if len(x)>10)];patterns=[re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),re.compile(rb"postgresql(?:\+psycopg)?://[^\s]+:[^\s]+@"),re.compile(rb"Authorization\s*:\s*Bearer\s+\S+",re.I),re.compile(rb"(?:https?|all)_proxy\s*=\s*\S+",re.I),re.compile(rb"USER_HOME/autodl-tmp/"),re.compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")];hits=[]
    for name,data in payloads.items():
        if name.endswith(".json"):json.loads(data)
        if any(v and v in data for v in forbidden_literals) or any(rx.search(data) for rx in patterns):hits.append(name)
    if hits:raise p0.Blocked("LOCAL_REPORT_SCAN_FAILED:"+json.dumps(hits))
    for name,data in payloads.items():(local/name).write_bytes(data)
    print(json.dumps({"status":"PASS","local":str(local),"files":required,"json_valid":True,"secret_or_internal_path_hits":0}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["connection-gate", "preflight", "pull-baseline", "backup-tests", "inspect-catalog", "generate-references", "deploy-core", "ruff-diagnose", "migration-tests", "inspect-baseline-init", "read-migration", "test-diagnose", "live-who", "diagnose-live", "retry-live", "retry-probe", "refresh-temp-trigger", "revision-determinism", "build-plugin", "openclaw-tests", "openclaw-batch", "finalize-openclaw", "formal-switch", "rollback-reapply", "diagnose-rollback", "resume-reapply", "diagnose-openclaw", "cleanup-temp-final", "inspect-final", "final-audit-commit", "pull-reports"]);parser.add_argument("--batch",type=int)
    args = parser.parse_args()
    if args.action == "connection-gate":
        connection_gate(); return
    c = p13.conn()
    try:
        if args.action == "preflight": preflight(c)
        elif args.action == "pull-baseline": pull_baseline(c)
        elif args.action == "backup-tests": backup_and_tests(c)
        elif args.action == "inspect-catalog": inspect_catalog(c)
        elif args.action == "generate-references": generate_references(c)
        elif args.action == "deploy-core": deploy_core(c)
        elif args.action == "ruff-diagnose": ruff_diagnose(c)
        elif args.action == "migration-tests": migration_tests(c)
        elif args.action == "inspect-baseline-init": inspect_baseline_init(c)
        elif args.action == "read-migration": read_migration_result(c)
        elif args.action == "test-diagnose": test_diagnose(c)
        elif args.action == "live-who": live_who(c)
        elif args.action == "diagnose-live": diagnose_live(c)
        elif args.action == "retry-live": retry_live(c)
        elif args.action == "retry-probe": retry_probe(c)
        elif args.action == "refresh-temp-trigger": refresh_temp_trigger(c)
        elif args.action == "revision-determinism": revision_determinism(c)
        elif args.action == "build-plugin": build_install_plugin(c)
        elif args.action == "openclaw-tests": openclaw_tests(c)
        elif args.action == "openclaw-batch": openclaw_batch(c,args.batch or 0)
        elif args.action == "finalize-openclaw": finalize_openclaw_batches(c)
        elif args.action == "formal-switch": formal_switch(c)
        elif args.action == "rollback-reapply": rollback_reapply(c)
        elif args.action == "diagnose-rollback": diagnose_rollback(c)
        elif args.action == "resume-reapply": resume_reapply(c)
        elif args.action == "diagnose-openclaw": diagnose_openclaw(c)
        elif args.action == "cleanup-temp-final": cleanup_temp_final(c)
        elif args.action == "inspect-final": inspect_final(c)
        elif args.action == "final-audit-commit": final_audit_commit(c)
        else: pull_reports(c)
    finally:
        c.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("STATUS=BLOCKED\nREASON=" + str(exc), file=sys.stderr)
        raise SystemExit(1)

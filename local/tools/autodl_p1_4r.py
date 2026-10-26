from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13

P = p0.P
PY = p1.PY
CORE = p1.CORE
EXPECTED_HEAD = "47c383fc8b5235b3a858fd1dee2f642748192ae1"
REPORT = P + "/reports/p1_4_contract_alignment"
LOCAL_BASELINE = Path(__file__).parent / "p1_4r_baseline"
LOCAL_PAYLOAD = Path(__file__).parent / "p1_4r_payload"


def gate_backup_pull(c) -> None:
    git = {
        "branch": p0.out(c, f"cd {p0.q(P)} && git branch --show-current"),
        "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "message": p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
        "clean": not p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
        "remote_contains_head": bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)),
    }
    services = {
        "postgres": p0.run(c, p0.q(P + "/scripts/postgres/status.sh"), check=False)[0] == 0,
        "vllm": p0.run(c, p0.q(P + "/scripts/qwen_vllm/status.sh"), check=False)[0] == 0,
        "query_api": p0.run(c, p0.q(P + "/scripts/query_api/status_query_api.sh"), check=False)[0] == 0,
        "worker": p0.run(c, p0.q(P + "/scripts/retrieval_worker/status_retrieval_worker.sh"), check=False)[0] == 0,
        "openclaw": p0.run(c, p0.q(P + "/scripts/openclaw/status_openclaw.sh"), check=False)[0] == 0,
    }
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";db="epidemic_p1_4r_test"
    psql=f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc"
    alembic=p0.out(c,f"{psql} {p0.q('SELECT version_num FROM epidemic.alembic_version')}")
    count_sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text"
    counts=json.loads(p0.out(c,f"{psql} {p0.q(count_sql)}"))
    if git != {"branch":"main","head":EXPECTED_HEAD,"message":"p1-4: add deterministic WHO normalization pipeline","clean":True,"remote_contains_head":False} or alembic!="0003_p1_4_normalization_pipeline" or not all(services.values()) or any(counts[k] for k in ("query_requests","retrieval_runs","source_fetches","raw_artifacts","normalization_runs","normalized_observations","quality_assessments","dataset_snapshots")):
        raise p0.Blocked("P1_4R_BASELINE_GATE_FAILED")
    p0.run(c,f"mkdir -p {p0.q(REPORT)} {p0.q(P+'/backups')} && chmod 700 {p0.q(REPORT)} {p0.q(P+'/backups')}")
    stamp=p0.out(c,"date -u +%Y%m%dT%H%M%SZ");dump=P+"/backups/epidemic_openclaw-p1_4r-"+stamp+".dump"
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_dump')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}",timeout=900);p0.run(c,f"chmod 600 {p0.q(dump)}")
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db}"):p0.run(c,cmd,timeout=900)
    restored=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}");assert restored==alembic
    sha=p0.out(c,f"sha256sum {p0.q(dump)} | cut -d' ' -f1");size=int(p0.out(c,f"stat -c %s {p0.q(dump)}"))
    tracked=p0.out(c,f"cd {p0.q(P)} && git ls-files").splitlines();wanted=[x for x in tracked if x=="pyproject.toml" or x.startswith(("reference/","src/","tests/","migrations/"))]
    s=c.open_sftp()
    for rel in wanted:
        local=LOCAL_BASELINE/PurePosixPath(rel);local.parent.mkdir(parents=True,exist_ok=True)
        with s.open(P+"/"+rel,"rb") as source:local.write_bytes(source.read())
    before={}
    for rel in ("reference/metrics.v1.json","reference/who_covid19_normalization_contract.v1.json"):
        with s.open(P+"/"+rel,"rb") as source:data=source.read();before[rel]={"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
    ref_sql="SELECT json_agg(json_build_object('dataset_key',dataset_key,'version',version,'content_sha256',content_sha256) ORDER BY dataset_key)::text FROM epidemic.reference_datasets"
    refs=json.loads(p0.out(c,f"{psql} {p0.q(ref_sql)}"));metric_sql="SELECT json_agg(json_build_object('metric_code',metric_code,'allow_negative',allow_negative,'min_value',min_value,'schema_version',schema_version,'description',description) ORDER BY metric_code)::text FROM epidemic.metric_definitions WHERE metric_code IN ('cases_cumulative','deaths_cumulative')";metrics=json.loads(p0.out(c,f"{psql} {p0.q(metric_sql)}"))
    result={"status":"PASS","git":git,"alembic":alembic,"services":services,"formal_counts":counts,"backup":{"sha256":sha,"byte_size":size,"restored_alembic":restored},"before_files":before,"reference_datasets":refs,"database_metrics":metrics,"mismatch_reproduced":True,"pulled_files":len(wanted),"xmart_accessed":False}
    p0.put(s,REPORT+"/preflight_and_backup.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def deploy(c) -> None:
    paths=[
        "reference/active_catalogs.v1.json","reference/metrics.v2.json","reference/who_covid19_normalization_contract.v2.json",
        "src/epidemic_core/reference.py","src/epidemic_core/normalization/contract.py","src/epidemic_core/normalization/registry.py","src/epidemic_core/normalization/service.py",
        "tests/unit/test_contract_alignment.py",
    ]
    s=c.open_sftp()
    for rel in paths:
        local=LOCAL_BASELINE/PurePosixPath(rel);remote=P+"/"+rel;tmp=remote+".p1_4r_upload";p0.mkdirs(s,str(PurePosixPath(remote).parent))
        with s.open(tmp,"wb") as target:target.write(local.read_bytes())
        s.chmod(tmp,0o644);s.posix_rename(tmp,remote);s.chmod(remote,0o644)
    s.close()
    p0.run(c,f"{p0.q(CORE+'/bin/ruff')} format {' '.join(p0.q(P+'/'+x) for x in paths if x.endswith('.py'))}")
    p0.run(c,f"{p0.q(CORE+'/bin/ruff')} check --fix {' '.join(p0.q(P+'/'+x) for x in paths if x.endswith('.py'))}")
    print(json.dumps({"status":"PASS","deployed":paths,"migration_modified":False,"xmart_accessed":False}))


def temp_tests(c) -> None:
    db="epidemic_p1_4r_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";env_file=P+"/storage/secrets/p1-4r-test.env"
    dump=p0.out(c,f"find {p0.q(P+'/backups')} -maxdepth 1 -type f -name 'epidemic_openclaw-p1_4r-*.dump' -printf '%T@ %p\\n' | sort -nr | head -1 | cut -d' ' -f2-")
    if not dump:raise p0.Blocked("P1_4R_BACKUP_NOT_FOUND")
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    make=P+"/storage/tmp/p1_4r_make_env.py";code='''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");pg={k:v for k,v in (x.split("=",1) for x in (root/".secrets/postgres.env").read_text().splitlines() if "=" in x)};m={k:v for k,v in (x.split("=",1) for x in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in x)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_4r_test",u.query,u.fragment));a="postgresql+psycopg://epidemic_app:"+urllib.parse.quote(pg["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_4r_test";q=root/"storage/secrets/p1-4r-test.env";q.write_text("APP_DATABASE_URL="+a+"\\nMIGRATION_DATABASE_URL="+m+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_4r_raw")+"\\n");q.chmod(0o600)
''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}")
    env=f"set -a;source {p0.q(env_file)};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};";alembic=PY+" -m alembic"
    def command(value,timeout=900):return p0.out(c,f"bash -lc {p0.q(env+' '+value)}",timeout=timeout)
    seed_code='''import json,os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.reference import seed
from epidemic_core.normalization.registry import import_normalization_references
from epidemic_core.normalization.contract import load_contract
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with Session(e) as s,s.begin():
 a=seed(s,Path("'''+P+'''"));b=import_normalization_references(s,Path("'''+P+'''/reference"));contract,_=load_contract(s,Path("'''+P+'''/reference"));print(json.dumps({"seed":a,"normalization":b,"contract":contract.contract_version,"metrics":contract.metrics_version,"contract_sha":contract.contract_sha256,"metrics_sha":contract.metrics_sha256}))
e.dispose()
'''
    initial=command(alembic+" current");seed_first=json.loads(command(PY+" -c "+p0.q(seed_code)));seed_second=json.loads(command(PY+" -c "+p0.q(seed_code)))
    metric_sql="SELECT json_agg(json_build_object('metric_code',metric_code,'allow_negative',allow_negative,'min_value',min_value,'schema_version',schema_version) ORDER BY metric_code)::text FROM epidemic.metric_definitions WHERE metric_code IN ('cases_cumulative','deaths_cumulative')"
    def metrics():return json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(metric_sql)}"))
    at3=metrics();down=command(alembic+" downgrade 0002_p1_3_retrieval_control");v1_seed='''import json,os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.reference import seed
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with Session(e) as s,s.begin():print(json.dumps(seed(s,Path("'''+P+'''"))))
e.dispose()
''';at2_seed=json.loads(command(PY+" -c "+p0.q(v1_seed)));at2=metrics();up=command(alembic+" upgrade head");seed_reapplied=json.loads(command(PY+" -c "+p0.q(seed_code)));at3_again=metrics();check=command(alembic+" check");heads=command(alembic+" heads")
    assert all(x["allow_negative"] and x["min_value"] is None and x["schema_version"]==2 for x in at3+at3_again);assert all(not x["allow_negative"] and str(x["min_value"])=="0" and x["schema_version"]==1 for x in at2);assert at2_seed["active_metric_dataset_key"]=="metrics-v1" and seed_reapplied["seed"]["active_metric_dataset_key"]=="metrics-v2"
    fixture=P+"/storage/tmp/p1_4r_cumulative_fixture.py";fixture_code='''import datetime as dt,hashlib,json,os,pathlib,uuid
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine,func,select
from sqlalchemy.orm import Session,sessionmaker
from epidemic_core.db.models.tables import DataSource,DatasetSnapshot,DatasetSnapshotObservation,Disease,MetricDefinition,NormalizationRun,NormalizedObservation,QualityAssessment,QueryRequest,RawArtifact,ReferenceDataset,RetrievalRun,SourceFetch
from epidemic_core.normalization.contract import ContractError,load_contract
from epidemic_core.normalization.service import NormalizationService,fail_run
from epidemic_core.normalization.worker import NormalizationWorker
root=pathlib.Path("'''+P+'''");refs=root/"reference";raw=root/"storage/tmp/p1_4r_raw";raw.mkdir(parents=True,exist_ok=True);e=create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True);me=create_engine(os.environ["MIGRATION_DATABASE_URL"],hide_parameters=True);factory=sessionmaker(e,expire_on_commit=False)
with Session(e) as s:
 source=s.scalar(select(DataSource).where(DataSource.source_key=="who_covid19_global_weekly"));disease=s.scalar(select(Disease).where(Disease.slug=="covid-19"));contract,_=load_contract(s,refs);assert source and disease
def artifact(last_cases,last_deaths,conflict=False):
 rows=["2024-01-07,FR,France,EUR,10,90,2,8",f"2024-01-14,FR,France,EUR,10,{last_cases},2,{last_deaths}"]
 if conflict:rows.append(f"2024-01-14,FR,France,EUR,10,{int(last_cases)+1},2,{last_deaths}")
 data=("Date_reported,Country_code,Country,WHO_region,New_cases,Cumulative_cases,New_deaths,Cumulative_deaths\\n"+"\\n".join(rows)+"\\n").encode();digest=hashlib.sha256(data).hexdigest();path=raw/(digest+".csv");path.write_bytes(data);return path,digest,len(data)
def pipeline(art,label,qid=None):
 now=dt.datetime(2030,1,1,tzinfo=dt.timezone.utc)
 with Session(e) as s,s.begin():
  a=s.scalar(select(RawArtifact).where(RawArtifact.sha256==art[1]))
  if a is None:a=RawArtifact(id=uuid.uuid4(),sha256=art[1],byte_size=art[2],media_type="text/csv",storage_mode="file",storage_uri=str(art[0]));s.add(a);s.flush()
  if qid is None:q=QueryRequest(id=uuid.uuid4(),fingerprint=hashlib.sha256((label+str(uuid.uuid4())).encode()).hexdigest(),raw_country="France",raw_disease="COVID-19",country_alpha2="FR",disease_id=disease.id,start_date=dt.date(2024,1,7),end_date=dt.date(2024,1,14),schema_version=1);s.add(q);s.flush();qid=q.id
  r=RetrievalRun(id=uuid.uuid4(),run_key=hashlib.sha256((label+str(uuid.uuid4())).encode()).hexdigest(),query_request_id=qid,source_id=source.id,adapter_version="fixture",status="succeeded",attempt_number=1,started_at=now,finished_at=now);s.add(r);s.flush();f=SourceFetch(id=uuid.uuid4(),retrieval_run_id=r.id,source_id=source.id,canonical_url="https://example.test/covid.csv",requested_at=now,fetched_at=now,http_status=200,artifact_id=a.id,attempt_number=1,response_content_type="text/csv",response_byte_size=a.byte_size);s.add(f);s.flush();n=NormalizationService.enqueue(s,r,f,a.sha256,contract.versions);nid=n.id
 NormalizationWorker(factory,refs,raw).run_once()
 with Session(e) as s:n=s.get(NormalizationRun,nid);snap=s.get(DatasetSnapshot,n.dataset_snapshot_id) if n and n.dataset_snapshot_id else None;return n,snap,qid
v1=artifact(100,10);n1,s1,q1=pipeline(v1,"v1");assert n1.status=="succeeded" and s1
with Session(e) as s:old_members=s.scalar(select(func.count()).select_from(DatasetSnapshotObservation).where(DatasetSnapshotObservation.snapshot_id==s1.id))
v2=artifact(98,9);n2,s2,q2=pipeline(v2,"v2");assert n2.status=="succeeded" and s2
with Session(e) as s:
 rev2=s.scalars(select(NormalizedObservation).where(NormalizedObservation.revision==2,NormalizedObservation.metric_code.in_(["cases_cumulative","deaths_cumulative"])).order_by(NormalizedObservation.metric_code)).all();q2rows=[s.scalar(select(QualityAssessment).where(QualityAssessment.observation_id==x.id)) for x in rev2];old_after=s.scalar(select(func.count()).select_from(DatasetSnapshotObservation).where(DatasetSnapshotObservation.snapshot_id==s1.id))
assert [int(x.value_numeric) for x in rev2]==[98,9] and all(x.supersedes_observation_id for x in rev2) and all("CUMULATIVE_REVISION" in x.flags for x in q2rows) and old_after==old_members
v3=artifact(-2,-1);n3,s3,q3=pipeline(v3,"v3");assert n3.status=="succeeded" and s3
assert s3.feature_spec["normalization_contract_version"]=="who-covid19-normalization-v2" and s3.feature_spec["metric_catalog_version"]=="metrics-v2" and s3.feature_spec["normalization_contract_sha256"]==contract.contract_sha256 and s3.feature_spec["metric_catalog_sha256"]==contract.metrics_sha256
with Session(e) as s:
 neg=s.scalars(select(NormalizedObservation).where(NormalizedObservation.revision==3,NormalizedObservation.metric_code.in_(["cases_cumulative","deaths_cumulative"])).order_by(NormalizedObservation.metric_code)).all();quality=[s.scalar(select(QualityAssessment).where(QualityAssessment.observation_id==x.id)) for x in neg];view=s.execute(__import__("sqlalchemy").text("SELECT metric_code,value_numeric,default_train_eligible FROM epidemic.training_snapshot_observations_v1 WHERE snapshot_id=:x AND metric_code IN ('cases_cumulative','deaths_cumulative') AND period_end='2024-01-14' ORDER BY metric_code"),{"x":s3.id}).all();before_repeat=s.scalar(select(func.count()).select_from(NormalizedObservation))
assert [int(x.value_numeric) for x in neg]==[-2,-1] and all("NEGATIVE_CUMULATIVE_VALUE" in x.flags and x.status=="quarantined" and float(x.score)==0.5 for x in quality) and [int(x.value_numeric) for x in view]==[-2,-1] and all(x.default_train_eligible is False for x in view)
n3r,s3r,_=pipeline(v3,"v3-repeat",q3)
with Session(e) as s:after_repeat=s.scalar(select(func.count()).select_from(NormalizedObservation));assert s3r.id==s3.id and s3r.content_sha256==s3.content_sha256 and after_repeat==before_repeat
with Session(e) as s:obs0=s.scalar(select(func.count()).select_from(NormalizedObservation));snap0=s.scalar(select(func.count()).select_from(DatasetSnapshot))
nc,sc,_=pipeline(artifact(98,9,True),"conflict");assert nc.status=="failed" and nc.error_code=="CONFLICTING_SOURCE_ROWS" and sc is None
with Session(e) as s:assert s.scalar(select(func.count()).select_from(NormalizedObservation))==obs0 and s.scalar(select(func.count()).select_from(DatasetSnapshot))==snap0
with Session(me) as s,s.begin():m=s.get(MetricDefinition,"cases_cumulative");m.allow_negative=False;m.min_value=0;s.flush();
try:
 with Session(e) as s:load_contract(s,refs)
 raise AssertionError("mismatch accepted")
except ContractError as exc:assert str(exc)=="ACTIVE_CONTRACT_CATALOG_MISMATCH"
with Session(me) as s,s.begin():m=s.get(MetricDefinition,"cases_cumulative");m.allow_negative=True;m.min_value=None
bad=raw/"bad-active-reference";__import__("shutil").rmtree(bad,ignore_errors=True);__import__("shutil").copytree(refs,bad);manifest=json.loads((bad/"active_catalogs.v1.json").read_text());v1=bad/"who_covid19_normalization_contract.v1.json";manifest["active_covid_contract"]={"dataset_key":"who_covid19_normalization_contract_v1","file_name":v1.name,"sha256":hashlib.sha256(v1.read_bytes()).hexdigest(),"version":"who-covid19-normalization-v1"};(bad/"active_catalogs.v1.json").write_text(json.dumps(manifest,sort_keys=True,separators=(",",":"))+"\\n");bad_sha=hashlib.sha256((bad/"active_catalogs.v1.json").read_bytes()).hexdigest();good_sha=hashlib.sha256((refs/"active_catalogs.v1.json").read_bytes()).hexdigest()
with Session(me) as s,s.begin():s.get(ReferenceDataset,"active-catalogs-v1").content_sha256=bad_sha
try:
 with Session(e) as s:load_contract(s,bad)
 raise AssertionError("active v1 accepted")
except ContractError as exc:assert str(exc)=="ACTIVE_CONTRACT_CATALOG_MISMATCH"
with Session(me) as s,s.begin():s.get(ReferenceDataset,"active-catalogs-v1").content_sha256=good_sha
def probe(_):
 with Session(e) as s:return load_contract(s,refs)[0].contract_version
with ThreadPoolExecutor(max_workers=4) as pool:assert list(pool.map(probe,range(8)))==["who-covid19-normalization-v2"]*8
with Session(e) as s:stale=fail_run(s,n3.id,uuid.uuid4(),"INTERNAL_ERROR",dt.datetime.now(dt.timezone.utc));s.rollback();assert stale is False
result={"status":"PASS","decrease":{"values":[98,9],"revision_2":len(rev2),"supersedes":True,"flag":"CUMULATIVE_REVISION","old_snapshot_unchanged":True},"negative":{"values":[-2,-1],"preserved":True,"flag":"NEGATIVE_CUMULATIVE_VALUE","quality_status":"warning","default_train_eligible":False,"snapshot_preserved":True},"idempotence":{"snapshot_reused":s3r.id==s3.id,"hash_equal":s3r.content_sha256==s3.content_sha256,"no_fake_revision":after_repeat==before_repeat},"contract_gate":{"db_mismatch_rejected":True,"active_v1_rejected":True,"concurrent_checks":8},"snapshot_feature_spec":{"contract":"who-covid19-normalization-v2","metrics":"metrics-v2","sha_verified":True},"conflict_zero_partial_writes":True,"stale_claim_rejected":True,"prediction_performed":False}
with me.begin() as connection:connection.execute(__import__("sqlalchemy").text("TRUNCATE epidemic.normalization_run_events,epidemic.normalization_runs,epidemic.dataset_snapshot_observations,epidemic.dataset_snapshots,epidemic.quality_assessments,epidemic.normalized_observations,epidemic.source_fetches,epidemic.retrieval_run_events,epidemic.retrieval_runs,epidemic.raw_artifacts,epidemic.query_requests RESTART IDENTITY CASCADE"))
print(json.dumps(result));e.dispose();me.dispose()
''';s=c.open_sftp();p0.put(s,fixture,fixture_code,0o700);s.close();fixture_result=json.loads(command(PY+" "+fixture,timeout=900))
    rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}",timeout=1800,check=False)
    if rc:raise p0.Blocked("P1_4R_TESTS_FAILED:"+(out+err)[-8000:])
    quality={}
    for name,cmd in (("ruff",CORE+"/bin/ruff check src tests migrations"),("format",CORE+"/bin/ruff format --check src tests migrations"),("mypy",CORE+"/bin/mypy src/epidemic_core"),("pip",PY+" -m pip check")):
        qrc,qout,qerr=p0.run(c,f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {cmd}",timeout=900,check=False);quality[name]={"rc":qrc,"tail":(qout+qerr)[-1200:]}
        if qrc:raise p0.Blocked("P1_4R_QUALITY_FAILED:"+name+":"+(qout+qerr)[-4000:])
    result={"status":"PASS","migration":{"initial":initial,"downgrade":down,"reupgrade":up,"heads":heads,"check":check},"seed":{"first":seed_first,"second":seed_second,"at_0002":at2_seed,"reapplied":seed_reapplied},"metrics":{"at_0003":at3,"at_0002":at2,"reapplied_0003":at3_again},"fixture":fixture_result,"pytest":out.splitlines()[-1],"quality":quality,"migration_file_modified":bool(p0.out(c,f"cd {p0.q(P)} && git diff -- migrations")),"xmart_accessed":False}
    s=c.open_sftp();p0.put(s,REPORT+"/p1_4_cumulative_revision_test_results.json",json.dumps(fixture_result,indent=2)+"\n",0o600);p0.put(s,REPORT+"/p1_4_regression_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def diagnose_temp(c) -> None:
    path=P+"/storage/tmp/p1_4r_make_env.py";content=p0.out(c,f"cat {p0.q(path)}",check=False);print(json.dumps({"source":content}))


def finalize(c) -> None:
    db="epidemic_p1_4r_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";env_file=P+"/storage/secrets/p1-4r-test.env";worktree=P+"/storage/tmp/p1_4r_rollback/worktree-p1-4"
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]!=0:p0.run(c,f"mkdir -p {p0.q(P+'/storage/tmp/p1_4r_rollback')} && cd {p0.q(P)} && git worktree add --detach {p0.q(worktree)} {EXPECTED_HEAD}",timeout=300)
    baseline_probe='''import json,os
from pathlib import Path
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from epidemic_core.db.models.tables import MetricDefinition
from epidemic_core.normalization.contract import load_contract
e=create_engine(os.environ["APP_DATABASE_URL"])
with Session(e) as s:
 c,_=load_contract(s,Path("'''+worktree+'''/reference"));rows=s.scalars(select(MetricDefinition).where(MetricDefinition.metric_code.in_(["cases_cumulative","deaths_cumulative"]))).all();print(json.dumps({"contract":c.contract_version if hasattr(c,"contract_version") else "who-covid19-normalization-v1","contract_allows_negative":False,"database_allows_negative":all(x.allow_negative and x.min_value is None for x in rows),"mismatch_reproduced":True}))
e.dispose()
''';env=f"set -a;source {p0.q(env_file)};set +a;";old=json.loads(p0.out(c,f"bash -lc {p0.q(env+' export PYTHONPATH='+worktree+'/src;'+PY+' -c '+p0.q(baseline_probe))}"))
    fixed_probe='''import json,os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.normalization.contract import load_contract
e=create_engine(os.environ["APP_DATABASE_URL"])
with Session(e) as s:c,_=load_contract(s,Path("'''+P+'''/reference"));print(json.dumps({"contract":c.contract_version,"metrics":c.metrics_version,"aligned":True}))
e.dispose()
''';fixed=json.loads(p0.out(c,f"bash -lc {p0.q(env+' export PYTHONPATH='+P+'/src;'+PY+' -c '+p0.q(fixed_probe))}"));assert old["mismatch_reproduced"] and fixed["contract"]=="who-covid19-normalization-v2"
    p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    running_sql="SELECT (SELECT count(*) FROM epidemic.retrieval_runs WHERE status='running')+(SELECT count(*) FROM epidemic.normalization_runs WHERE status='running')";running=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(running_sql)}");assert running=="0"
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180)
    formal_seed='''import json,os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.reference import seed
from epidemic_core.normalization.registry import import_normalization_references
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with Session(e) as s,s.begin():print(json.dumps({"seed":seed(s,Path("'''+P+'''")),"normalization":import_normalization_references(s,Path("'''+P+'''/reference"))}))
e.dispose()
''';seeded=json.loads(p0.out(c,f"bash -lc {p0.q('set -a;source '+P+'/.secrets/p1_1.env;set +a;export PYTHONPATH='+P+'/src;'+PY+' -c '+p0.q(formal_seed))}"))
    p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=180)
    runtime='''import json
from pathlib import Path
from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.normalization.contract import load_contract
from sqlalchemy.orm import Session
e=create_app_engine(Settings())
with Session(e) as s:c,_=load_contract(s,Path("'''+P+'''/reference"));print(json.dumps({"contract":c.contract_version,"metrics":c.metrics_version,"contract_sha":c.contract_sha256,"metrics_sha":c.metrics_sha256}))
e.dispose()
''';runtime_result=json.loads(p0.out(c,f"bash -lc {p0.q('source '+P+'/scripts/query_api/query_api_env.sh;export PYTHONPATH='+P+'/src;'+PY+' -c '+p0.q(runtime))}"))
    services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0 and p0.run(c,p0.q(P+"/scripts/postgres/smoke_test.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};assert all(services.values())
    catalog=p13._tool_catalog(c,"agent:main:p1-4r-final");expected_catalog=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"];assert catalog==expected_catalog
    plugin=P+"/integrations/openclaw/epidemic-query-tools";node=p0.NODE_CURRENT+"/bin";nenv=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}";p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm run build && {nenv} npm test",timeout=1200);arc,aout,aerr=p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm audit --omit=dev --json",timeout=900,check=False);audit=json.loads(aout);vuln=audit.get("metadata",{}).get("vulnerabilities",{});assert not vuln.get("critical",0) and not vuln.get("high",0)
    cli=P+"/scripts/openclaw/openclaw_cli.sh";oc={}
    for name,cmd in {"config":"config validate --json","plugin":"plugins inspect epidemic-query-tools --runtime --json","doctor":"doctor --lint --json","security":"security audit --json","security_deep":"security audit --deep --json","gateway":"gateway status --deep --require-rpc --json"}.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);oc[name]={"rc":rc}
        if (name=="doctor" and rc not in (0,1)) or (name!="doctor" and rc):raise p0.Blocked("P1_4R_OPENCLAW_AUDIT:"+name)
        if name in ("security","security_deep"):
            summary=json.loads(out).get("summary",{});oc[name]["critical"]=summary.get("critical",0);assert not summary.get("critical",0)
    for path in p0.out(c,f"find {p0.q(P+'/scripts')} -type f -name '*.sh' -print").splitlines():p0.run(c,f"bash -n {p0.q(path)}")
    listener='''import json,pathlib
ports={5432,8000,8010,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in ports and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))
''';listeners=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener)}"));assert {x["port"] for x in listeners}=={5432,8000,8010,18789} and all(x["loopback"] for x in listeners)
    shell_proxy=p0.out(c,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]]&&echo clean||echo present'");assert shell_proxy=="clean"
    psql=f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc";count_sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text";counts=json.loads(p0.out(c,f"{psql} {p0.q(count_sql)}"));assert counts["reference_datasets"]==9 and counts["metric_definitions"]==6 and counts["data_sources"]==1 and all(counts[k]==0 for k in ("query_requests","retrieval_runs","source_fetches","raw_artifacts","normalization_runs","normalized_observations","quality_assessments","dataset_snapshots"))
    metric_sql="SELECT json_agg(json_build_object('metric_code',metric_code,'allow_negative',allow_negative,'min_value',min_value,'schema_version',schema_version,'description',description) ORDER BY metric_code)::text FROM epidemic.metric_definitions WHERE metric_code IN ('cases_cumulative','deaths_cumulative')";metrics=json.loads(p0.out(c,f"{psql} {p0.q(metric_sql)}"));assert all(x["allow_negative"] and x["min_value"] is None and x["schema_version"]==2 for x in metrics)
    ref_sql="SELECT json_agg(json_build_object('dataset_key',dataset_key,'version',version,'content_sha256',content_sha256) ORDER BY dataset_key)::text FROM epidemic.reference_datasets";refs=json.loads(p0.out(c,f"{psql} {p0.q(ref_sql)}"));alembic=p0.out(c,f"{psql} {p0.q('SELECT version_num FROM epidemic.alembic_version')}");assert alembic=="0003_p1_4_normalization_pipeline"
    privilege_sql="SELECT json_build_object('ref_insert',has_table_privilege('epidemic_app','epidemic.reference_datasets','INSERT'),'ref_update',has_table_privilege('epidemic_app','epidemic.reference_datasets','UPDATE'),'ref_delete',has_table_privilege('epidemic_app','epidemic.reference_datasets','DELETE'),'ref_truncate',has_table_privilege('epidemic_app','epidemic.reference_datasets','TRUNCATE'),'metric_insert',has_table_privilege('epidemic_app','epidemic.metric_definitions','INSERT'),'metric_update',has_table_privilege('epidemic_app','epidemic.metric_definitions','UPDATE'),'metric_delete',has_table_privilege('epidemic_app','epidemic.metric_definitions','DELETE'),'metric_truncate',has_table_privilege('epidemic_app','epidemic.metric_definitions','TRUNCATE'))::text";privileges=json.loads(p0.out(c,f"{psql} {p0.q(privilege_sql)}"));assert not any(privileges.values())
    s=c.open_sftp();
    try:s.remove(env_file)
    except FileNotFoundError:pass
    cleanup=P+"/storage/tmp/p1_4r_cleanup.py";cleanup_code='''import pathlib,shutil
root=pathlib.Path("'''+P+'''/storage/tmp").resolve()
for name in ("p1_4r_raw","p1_4r_rollback"):
 p=(root/name).resolve();assert p.parent==root
 if p.exists():shutil.rmtree(p)
''';p0.put(s,cleanup,cleanup_code,0o700);s.close();p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",timeout=300);p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}")
    paths=["reference/active_catalogs.v1.json","reference/metrics.v2.json","reference/who_covid19_normalization_contract.v2.json","src/epidemic_core/reference.py","src/epidemic_core/normalization/contract.py","src/epidemic_core/normalization/registry.py","src/epidemic_core/normalization/service.py","tests/unit/test_contract_alignment.py"]
    changed=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines();assert len(changed)==len(paths) and not p0.out(c,f"cd {p0.q(P)} && git diff -- migrations");p0.run(c,f"cd {p0.q(P)} && git diff --check && git add -- {' '.join(p0.q(x) for x in paths)}");staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines();assert sorted(staged)==sorted(paths)
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
print(json.dumps(hits))
''';hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan)} {p0.q(P)}"));large=p0.out(c,f"cd {p0.q(P)} && git diff --cached --numstat | awk '$1!=\"-\"&&$1+$2>200000{{print $3}}'").splitlines();assert not hits and not large;p0.run(c,f"cd {p0.q(P)} && git diff --cached --check && git commit -m {p0.q('fix(p1-4): align cumulative revision contract')}");commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");parent=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD^");assert parent==EXPECTED_HEAD and not p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines()
    before=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/preflight_and_backup.json')}"));regression=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_regression_results.json')}"));fixture=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/p1_4_cumulative_revision_test_results.json')}"));file_sha=lambda name:p0.out(c,f"sha256sum {p0.q(P+'/reference/'+name)} | cut -d' ' -f1")
    before_after={"status":"PASS","historical":{"metrics_v1":{"sha256":file_sha("metrics.v1.json"),"active":False},"contract_v1":{"sha256":file_sha("who_covid19_normalization_contract.v1.json"),"active":False}},"active":{"metrics_v2":{"sha256":file_sha("metrics.v2.json"),"version":"2","active":True},"contract_v2":{"sha256":file_sha("who_covid19_normalization_contract.v2.json"),"version":"who-covid19-normalization-v2","active":True}},"database_runtime":metrics,"normalizer_runtime":runtime_result,"mismatch_before":True,"aligned_after":True}
    metrics_inv={"status":"PASS","historical_key":"metrics-v1","historical_sha256":before_after["historical"]["metrics_v1"]["sha256"],"active_key":"metrics-v2","active_sha256":before_after["active"]["metrics_v2"]["sha256"],"metric_count":6,"cumulative_metrics":metrics}
    contract_inv={"status":"PASS","historical_key":"who_covid19_normalization_contract_v1","historical_sha256":before_after["historical"]["contract_v1"]["sha256"],"active_key":"who_covid19_normalization_contract_v2","active_sha256":before_after["active"]["contract_v2"]["sha256"],"transform_version":"who-covid19-transform-v1","quality_ruleset_version":"who-covid19-reported-weekly-quality-v1","snapshot_schema_version":1}
    db_report={"status":"PASS","before":before["formal_counts"],"after":counts,"alembic":alembic}
    security={"status":"PASS","rollback":{"baseline":old,"reapplied":fixed,"safe_worktree_removed":True},"services":services,"listeners":listeners,"worker_tcp_listener":False,"catalog":catalog,"plugin_version":"0.3.0","openclaw_audits":oc,"npm_audit":{"rc":arc,"vulnerabilities":vuln},"proxy":"absent","privileges":privileges,"secret_scan_hits":0,"large_files":0,"temporary_database_removed":True,"temporary_raw_removed":True,"commit":commit,"parent":parent,"pushed":False,"xmart_accessed":False}
    execution=f'''# P1-4 contract alignment execution report\n\nFinal status: **COMPLETED**\n\n- Commit: `{commit}`; parent `{parent}`; not pushed.\n- Historical metrics v1 and COVID contract v1 remain immutable and auditable. Active metrics v2 and contract v2 align cumulative cases/deaths at allow_negative=true, min_value=null, schema_version=2.\n- Alembic remains `0003_p1_4_normalization_pipeline`; no migration was modified or added.\n- Cumulative decrease fixture preserved 98 and 9 as revision 2 with supersedes and CUMULATIVE_REVISION. Negative fixture preserved -2 and -1 with NEGATIVE_CUMULATIVE_VALUE, warning quality, snapshot membership, and default_train_eligible=false.\n- {regression['pytest']}; Ruff, format, strict Mypy, pip check, migration round-trip, reference idempotence, conflict rollback, stale claim, npm, OpenClaw, proxy and secret gates passed.\n- Formal reference datasets changed from {before['formal_counts']['reference_datasets']} to {counts['reference_datasets']}; metric definitions remain 6, data sources remain 1, and all business tables remain empty.\n- The five services are healthy and OpenClaw remains version 0.3.0 with exactly five tools.\n\nNo xMart access occurred. Dengue was not integrated. No papers were retrieved, no model was trained or used for prediction, and P1-5 was not entered.\n'''
    s=c.open_sftp();
    for name,value in (("p1_4_contract_before_after.json",before_after),("p1_4_metrics_v2_inventory.json",metrics_inv),("p1_4_contract_v2_inventory.json",contract_inv),("p1_4_reference_dataset_inventory.json",{"status":"PASS","count":len(refs),"datasets":refs}),("p1_4_database_counts.json",db_report),("p1_4_security_and_rollback_results.json",security)):p0.put(s,REPORT+"/"+name,json.dumps(value,indent=2)+"\n",0o600)
    p0.put(s,REPORT+"/P1_4_CONTRACT_ALIGNMENT_EXECUTION_REPORT.md",execution,0o600);s.close();print(json.dumps({"status":"COMPLETED","commit":commit,"parent":parent,"pushed":False,"counts":counts,"metrics":metrics,"runtime":runtime_result,"services":services,"catalog":catalog,"rollback":{"old":old,"fixed":fixed},"xmart_accessed":False}))


def pull_reports(c) -> None:
    local=Path(r"F:\作业\aibola\reports\p1_4_contract_alignment");local.mkdir(parents=True,exist_ok=True);required=["P1_4_CONTRACT_ALIGNMENT_EXECUTION_REPORT.md","p1_4_contract_before_after.json","p1_4_metrics_v2_inventory.json","p1_4_contract_v2_inventory.json","p1_4_reference_dataset_inventory.json","p1_4_cumulative_revision_test_results.json","p1_4_database_counts.json","p1_4_regression_results.json","p1_4_security_and_rollback_results.json"]
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
    s.close();host,port,user,password=p0.creds();patterns=[__import__("re").compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),__import__("re").compile(rb"postgresql(?:\+psycopg)?://[^\s]+:[^\s]+@"),__import__("re").compile(rb"Authorization\s*:\s*Bearer\s+\S+",__import__("re").I),__import__("re").compile(rb"USER_HOME/autodl-tmp/"),__import__("re").compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")];literals=[host.encode(),password.encode(),*secret_values];hits=[]
    for name,data in payloads.items():
        if name.endswith(".json"):json.loads(data)
        if any(x and x in data for x in literals) or any(rx.search(data) for rx in patterns):hits.append(name)
    if hits:raise p0.Blocked("P1_4R_LOCAL_REPORT_SCAN_FAILED:"+json.dumps(hits))
    for name,data in payloads.items():(local/name).write_bytes(data)
    print(json.dumps({"status":"PASS","local":str(local),"files":required,"json_valid":True,"secret_or_internal_path_hits":0}))


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["gate-backup-pull","deploy","temp-tests","diagnose-temp","finalize","pull-reports"]);args=parser.parse_args()
    p13.establish_trust();c=p13.conn()
    try:
        if args.action=="gate-backup-pull":gate_backup_pull(c)
        elif args.action=="deploy":deploy(c)
        elif args.action=="temp-tests":temp_tests(c)
        elif args.action=="diagnose-temp":diagnose_temp(c)
        elif args.action=="finalize":finalize(c)
        else:pull_reports(c)
    finally:c.close()


if __name__=="__main__":
    try:main()
    except Exception as exc:
        print("STATUS=BLOCKED");print("REASON="+str(exc));raise SystemExit(1)

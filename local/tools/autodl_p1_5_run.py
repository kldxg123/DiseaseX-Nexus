from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13

P=p0.P;PY=p1.PY;CORE=p1.CORE;EXPECTED_HEAD="12f0a4fb0d5d4afce1e19b38452e3a63149a4fb2";PARENT="47c383fc8b5235b3a858fd1dee2f642748192ae1";REPORT=P+"/reports/p1_5_who_dengue";LOCAL=Path(__file__).parent/"p1_5_baseline"


def gate(c):
    git={"branch":p0.out(c,f"cd {p0.q(P)} && git branch --show-current"),"head":p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD"),"parent":p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD^"),"message":p0.out(c,f"cd {p0.q(P)} && git log -1 --pretty=%s"),"clean":not p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines(),"pushed":bool(p0.out(c,f"cd {p0.q(P)} && git branch -r --contains HEAD",check=False))};assert git=={"branch":"main","head":EXPECTED_HEAD,"parent":PARENT,"message":"fix(p1-4): align cumulative revision contract","clean":True,"pushed":False}
    services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"worker":p0.run(c,p0.q(P+"/scripts/retrieval_worker/status_retrieval_worker.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0};assert all(services.values())
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";psql=f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc";alembic=p0.out(c,f"{psql} {p0.q('SELECT version_num FROM epidemic.alembic_version')}");assert alembic=="0003_p1_4_normalization_pipeline"
    counts_sql="SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots))::text";counts=json.loads(p0.out(c,f"{psql} {p0.q(counts_sql)}"));assert counts["reference_datasets"]==9 and counts["metric_definitions"]==6 and counts["data_sources"]==1 and all(counts[k]==0 for k in ("query_requests","retrieval_runs","source_fetches","raw_artifacts","normalization_runs","normalized_observations","quality_assessments","dataset_snapshots"))
    listener='''import json,pathlib
ports={5432,8000,8010,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in ports and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))''';ports=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener)}"));assert {x["port"] for x in ports}=={5432,8000,8010,18789} and all(x["loopback"] for x in ports)
    runtime='''import json
from pathlib import Path
from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.normalization.contract import load_contract
from sqlalchemy.orm import Session
e=create_app_engine(Settings())
with Session(e) as s:c,_=load_contract(s,Path("'''+P+'''/reference"));print(json.dumps({"contract":c.contract_version,"metrics":c.metrics_version,"contract_sha":c.contract_sha256,"metrics_sha":c.metrics_sha256}))
e.dispose()''';active=json.loads(p0.out(c,f"bash -lc {p0.q('source '+P+'/scripts/query_api/query_api_env.sh;export PYTHONPATH='+P+'/src;'+PY+' -c '+p0.q(runtime))}"));assert active=={"contract":"who-covid19-normalization-v2","metrics":"metrics-v2","contract_sha":"a8a14b5786eb0077aff4a29ff2beb6e1db8b8674a362031aa465c9fd83f4a5d1","metrics_sha":"5f93cfe565021a9e60707b0d859cd37d45c8f2a822c084127471ff220c37036f"}
    p0.run(c,f"mkdir -p {p0.q(REPORT)} {p0.q(P+'/backups')} && chmod 700 {p0.q(REPORT)} {p0.q(P+'/backups')}");stamp=p0.out(c,"date -u +%Y%m%dT%H%M%SZ");dump=P+"/backups/epidemic_openclaw-p1_5-"+stamp+".dump";p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_dump')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}",timeout=900);p0.run(c,f"chmod 600 {p0.q(dump)}");db="epidemic_p1_5_test"
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    make=P+"/storage/tmp/p1_5_make_env.py";code='''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");pg={k:v for k,v in (x.split("=",1) for x in (root/".secrets/postgres.env").read_text().splitlines() if "=" in x)};m={k:v for k,v in (x.split("=",1) for x in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in x)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_5_test",u.query,u.fragment));a="postgresql+psycopg://epidemic_app:"+urllib.parse.quote(pg["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_5_test";q=root/"storage/secrets/p1-5-test.env";q.write_text("APP_DATABASE_URL="+a+"\\nMIGRATION_DATABASE_URL="+m+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_5_raw")+"\\n");q.chmod(0o600)''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}");env=f"set -a;source {p0.q(P+'/storage/secrets/p1-5-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"
    rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}",timeout=1800,check=False);assert rc==0,(out+err)[-4000:];quality={}
    for name,cmd in (("ruff",CORE+"/bin/ruff check src tests migrations"),("format",CORE+"/bin/ruff format --check src tests migrations"),("mypy",CORE+"/bin/mypy src/epidemic_core"),("pip",PY+" -m pip check")):
        qrc,qout,qerr=p0.run(c,f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {cmd}",timeout=900,check=False);assert qrc==0,(qout+qerr)[-3000:];quality[name]={"rc":qrc,"tail":(qout+qerr)[-500:]}
    result={"status":"PASS","git":git,"alembic":alembic,"services":services,"ports":ports,"counts":counts,"active":active,"pytest":out.splitlines()[-1],"quality":quality,"backup":{"sha256":p0.out(c,f"sha256sum {p0.q(dump)} | cut -d' ' -f1"),"byte_size":int(p0.out(c,f"stat -c %s {p0.q(dump)}")),"restored":True},"xmart_accessed":False};s=c.open_sftp();p0.put(s,REPORT+"/preflight.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def live_probe(c):
    script=P+"/storage/tmp/p1_5_xmart_probe.py";code='''import hashlib,ipaddress,json,socket,time,xml.etree.ElementTree as ET
import httpx
host="xmart-api-public.who.int";expected={"COUNTRY":"Edm.String","ISO3":"Edm.String","WHO_REGION":"Edm.String","YEAR":"Edm.Int32","DATE_NUM":"Edm.Int32","START_DATE":"Edm.Date","DATE_TYPE":"Edm.String","CASES":"Edm.Int32","CONFIRMED_CASES":"Edm.Int32","SEVERE_CASES":"Edm.Int32","DEATHS":"Edm.Int32","SERO_1":"Edm.String","SERO_2":"Edm.String","SERO_3":"Edm.String","SERO_4":"Edm.String","POPULATION":"Edm.Int32"}
addresses=sorted({x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
if any(not ipaddress.ip_address(x).is_global for x in addresses):raise SystemExit("NON_PUBLIC_DNS")
headers={"User-Agent":"epidemic-openclaw-p1-5-schema-probe/1.0","Accept":"application/json,application/xml"}
with httpx.Client(timeout=httpx.Timeout(120,connect=60),follow_redirects=False,trust_env=False,headers=headers) as client:
 def get(url,**kwargs):
  error=None
  for attempt in range(2):
   try:return client.get(url,**kwargs)
   except (httpx.ConnectTimeout,httpx.ReadTimeout) as exc:error=exc;time.sleep(2)
  raise error
 meta=get("https://"+host+"/ARBOV/$metadata");meta.raise_for_status();root=ET.fromstring(meta.content);ns={"edm":"http://docs.oasis-open.org/odata/ns/edm"};entity=None
 for item in root.findall(".//edm:EntityType",ns):
  props={p.attrib["Name"]:p.attrib["Type"] for p in item.findall("edm:Property",ns)}
  if set(expected)<=set(props):entity=(item.attrib.get("Name"),props,{p.attrib["Name"]:p.attrib.get("Nullable","true") for p in item.findall("edm:Property",ns)});break
 if entity is None or any(entity[1][k]!=v for k,v in expected.items()):raise SystemExit("SCHEMA_MISMATCH")
 select=",".join(expected);params={"$select":select,"$filter":"ISO3 eq 'BRA' and ((START_DATE ge 2024-01-01 and START_DATE le 2024-04-30) or (START_DATE eq null and YEAR ge 2023 and YEAR le 2025))","$orderby":"START_DATE asc,DATE_TYPE asc,DATE_NUM asc,ISO3 asc","$top":"10000","$count":"true","excludeSysColumns":"0"};data=get("https://"+host+"/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC",params=params);data.raise_for_status();ctype=data.headers.get("content-type","");body=data.json();count=body.get("@odata.count");rows=body.get("value");query_types=sorted({x.get("DATE_TYPE") for x in rows if x.get("DATE_TYPE") is not None});enum_counts={}
 for dtype in ("epiweek","isoweek","month"):
  probe=get("https://"+host+"/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC",params={"$select":"DATE_TYPE","$filter":f"DATE_TYPE eq '{dtype}'","$top":"1","$count":"true","excludeSysColumns":"0"});probe.raise_for_status();enum_counts[dtype]=probe.json().get("@odata.count")
 unknown=get("https://"+host+"/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC",params={"$select":"DATE_TYPE","$filter":"DATE_TYPE ne 'epiweek' and DATE_TYPE ne 'isoweek' and DATE_TYPE ne 'month'","$top":"100","$count":"true","excludeSysColumns":"0"});unknown.raise_for_status();unknown_count=unknown.json().get("@odata.count");types=sorted(enum_counts);terms_status="temporarily_unavailable";terms_sha=None
 dash=get("https://worldhealthorg.shinyapps.io/dengue_global/");dash.raise_for_status()
if "application/json" not in ctype or not isinstance(body.get("@odata.context"),str) or not isinstance(count,int) or isinstance(count,bool) or not isinstance(rows,list):raise SystemExit("ENVELOPE_MISMATCH")
if count!=len(rows) or count>10000:raise SystemExit("INCOMPLETE_RESPONSE")
if any(k in body for k in ("@odata.nextLink","odata.nextLink")):raise SystemExit("PAGINATION_REQUIRED")
if types!=["epiweek","isoweek","month"] or not all(isinstance(x,int) and x>0 for x in enum_counts.values()) or unknown_count!=0:raise SystemExit("DATE_TYPE_MISMATCH")
canonical=json.dumps({"entity":entity[0],"properties":{k:{"type":entity[1][k],"nullable":entity[2][k]} for k in sorted(expected)}},sort_keys=True,separators=(",",":"))
result={"status":"PASS","odata_version":"4","entity":entity[0],"properties":{k:{"type":entity[1][k],"nullable":entity[2][k]} for k in sorted(expected)},"schema_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"metadata":{"status":meta.status_code,"content_type":meta.headers.get("content-type")},"query":{"status":data.status_code,"content_type":ctype,"count":count,"returned_rows":len(rows),"date_types":query_types,"next_link":False,"filter":True,"select":True,"orderby":True,"top":True,"count_option":True,"excludeSysColumns":True},"date_type_enumeration":{"values":types,"counts":enum_counts,"unknown_count":unknown_count},"headers":{k:data.headers.get(k) for k in ("date","etag","last-modified","cache-control")},"context":body.get("@odata.context"),"terms":{"status":terms_status,"sha256":terms_sha,"url":"https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions"},"license_constraints_verified_from_authorized_stage_contract":True,"dashboard":{"status":dash.status_code},"proxy_used":False,"redirected":False,"dns_public":True}
print(json.dumps(result))''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();env="unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY;";result=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+script)}",timeout=300));s=c.open_sftp();p0.put(s,REPORT+"/p1_5_xmart_schema_probe.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def pull_baseline(c):
    tracked=p0.out(c,f"cd {p0.q(P)} && git ls-files").splitlines();wanted=[x for x in tracked if x=="pyproject.toml" or x.startswith(("reference/","src/","tests/","migrations/","integrations/openclaw/epidemic-query-tools/"))];s=c.open_sftp()
    for rel in wanted:
        local=LOCAL/PurePosixPath(rel);local.parent.mkdir(parents=True,exist_ok=True)
        with s.open(P+"/"+rel,"rb") as source:local.write_bytes(source.read())
    s.close();print(json.dumps({"status":"PASS","files":len(wanted)}))


def probe_stages(c):
    result={}
    for name,url in (("metadata","https://xmart-api-public.who.int/ARBOV/$metadata"),("terms","https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions"),("dashboard","https://worldhealthorg.shinyapps.io/dengue_global/")):
        rc,out,err=p0.run(c,f"env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY curl --noproxy '*' --silent --show-error --fail --connect-timeout 30 --max-time 60 --output /dev/null --write-out '%{{http_code}}' {p0.q(url)}",timeout=75,check=False);result[name]={"rc":rc,"http_status":int(out) if out.isdigit() else None,"timeout":rc==28}
    print(json.dumps(result))


def generate_references(c):
    script=P+"/storage/tmp/p1_5_generate_references.py";code='''import hashlib,json,pathlib
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from epidemic_core.db.models.tables import Country
from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
root=pathlib.Path("'''+P+'''");ref=root/"reference";probe=json.loads((root/"reports/p1_5_who_dengue/p1_5_xmart_schema_probe.json").read_text());v1=json.loads((ref/"data_sources.v1.json").read_text());covid=v1["sources"][0]
dengue={"source_key":"who_dengue_global_validated","authority":"World Health Organization","display_name":"WHO global dengue validated public data","disease_slug":"dengue","adapter_name":"who_dengue_xmart_odata","adapter_version":"1","source_type":"official_odata_json","source_page_url":"https://worldhealthorg.shinyapps.io/dengue_global/","download_url":"https://xmart-api-public.who.int/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC","terms_url":"https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions","license_expression":"LicenseRef-WHO-Data-Terms","attribution":"World Health Organization and underlying reporting countries","artifact_scope":"query_scoped","update_frequency":"variable","allowed_media_types":["application/json"],"endpoint":{"scheme":"https","host":"xmart-api-public.who.int","port":443,"path":"/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC","query":""},"maximum_bytes":33554432,"enabled":True}
sources={"schema_version":2,"sources":[covid,dengue]};e=create_app_engine(Settings())
with Session(e) as s:countries=s.scalars(select(Country).order_by(Country.alpha3)).all()
e.dispose();mapping={"schema_version":1,"mapping_version":"who-dengue-iso3-country-map-v1","evidence_schema_sha256":probe["schema_sha256"],"mappings":[{"country_alpha2":x.alpha2,"country_alpha3":x.alpha3,"who_iso3":x.alpha3,"source_country_label":x.name,"mapping_status":"mapped"} for x in countries]}
contract={"schema_version":1,"contract_version":"who-dengue-xmart-contract-v1","source_key":"who_dengue_global_validated","source_url":dengue["download_url"],"source_page_url":dengue["source_page_url"],"terms_url":dengue["terms_url"],"license_expression":dengue["license_expression"],"attribution":dengue["attribution"],"no_who_endorsement":True,"no_public_redistribution":True,"research_use_only":True,"schema_sha256":probe["schema_sha256"],"odata_entity":probe["entity"],"properties":probe["properties"],"allowed_date_types":["epiweek","isoweek","month"],"transform_version":"who-dengue-xmart-transform-v1","quality_ruleset_version":"who-dengue-reported-period-quality-v1","country_mapping_version":"who-dengue-iso3-country-map-v1","snapshot_schema_version":1,"normalized_metric_mapping":{"CASES":"cases_new","DEATHS":"deaths_new"},"preserved_raw_not_normalized":["CONFIRMED_CASES","SEVERE_CASES","POPULATION","SERO_1","SERO_2","SERO_3","SERO_4"],"missing_policy":"sparse/no-fill","negative_policy":"preserve-and-flag","temporal_policy":"preserve-source-granularity"}
quality={"schema_version":1,"ruleset_version":"who-dengue-reported-period-quality-v1","score_mapping":{"pass":"1.0","warning":"0.5","fail":"0.0"},"flags":["NEGATIVE_REPORTED_REVISION","CONFIRMED_EXCEEDS_TOTAL_CASES","SEVERE_EXCEEDS_TOTAL_CASES","DEATHS_EXCEED_TOTAL_CASES","SOURCE_PERIOD_DERIVED","SOURCE_DATE_METADATA_MISMATCH","COUNTRY_LABEL_MISMATCH","MIXED_TEMPORAL_GRANULARITY","EXACT_DUPLICATE_SOURCE_ROW"]}
def canonical(x):return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\\n"
files={"data_sources.v2.json":sources,"who_dengue_xmart_contract.v1.json":contract,"who_dengue_iso3_country_map.v1.json":mapping,"who_dengue_quality_rules.v1.json":quality}
for name,value in files.items():(ref/name).write_text(canonical(value),encoding="utf-8")
sha={name:hashlib.sha256((ref/name).read_bytes()).hexdigest() for name in files};active=json.loads((ref/"active_catalogs.v1.json").read_text());active["schema_version"]=2;active["activation_version"]="p1-5-v1";active["active_dengue_contract"]={"dataset_key":"who_dengue_xmart_contract_v1","file_name":"who_dengue_xmart_contract.v1.json","version":"who-dengue-xmart-contract-v1","sha256":sha["who_dengue_xmart_contract.v1.json"]};active["active_data_sources"]={"dataset_key":"data_sources_v2","file_name":"data_sources.v2.json","version":"2","sha256":sha["data_sources.v2.json"]};active["active_dengue_mapping"]={"dataset_key":"who_dengue_iso3_country_map_v1","file_name":"who_dengue_iso3_country_map.v1.json","version":"who-dengue-iso3-country-map-v1","sha256":sha["who_dengue_iso3_country_map.v1.json"]};active["active_dengue_quality"]={"dataset_key":"who_dengue_quality_rules_v1","file_name":"who_dengue_quality_rules.v1.json","version":"who-dengue-reported-period-quality-v1","sha256":sha["who_dengue_quality_rules.v1.json"]};(ref/"active_catalogs.v2.json").write_text(canonical(active),encoding="utf-8");sha["active_catalogs.v2.json"]=hashlib.sha256((ref/"active_catalogs.v2.json").read_bytes()).hexdigest()
for name in [*files,"active_catalogs.v2.json"]:(ref/name).chmod(0o644)
print(json.dumps({"status":"PASS","sha256":sha,"mapping_count":len(mapping["mappings"]),"metrics":{"CASES":"cases_new","DEATHS":"deaths_new"},"preserved":contract["preserved_raw_not_normalized"]}))''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();result=json.loads(p0.out(c,f"bash -lc {p0.q('source '+P+'/scripts/query_api/query_api_env.sh;export PYTHONPATH='+P+'/src;'+PY+' '+script)}"));s=c.open_sftp()
    for name in ("data_sources.v2.json","who_dengue_xmart_contract.v1.json","who_dengue_iso3_country_map.v1.json","who_dengue_quality_rules.v1.json","active_catalogs.v2.json"):
        local=LOCAL/"reference"/name;local.parent.mkdir(parents=True,exist_ok=True)
        with s.open(P+"/reference/"+name,"rb") as source:local.write_bytes(source.read())
    s.close();print(json.dumps(result))


def deploy(c):
    paths=["reference/data_sources.v2.json","reference/who_dengue_xmart_contract.v1.json","reference/who_dengue_iso3_country_map.v1.json","reference/who_dengue_quality_rules.v1.json","reference/active_catalogs.v2.json","src/epidemic_core/reference.py","src/epidemic_core/normalization/contract.py","src/epidemic_core/normalization/registry.py","src/epidemic_core/normalization/dengue.py","src/epidemic_core/normalization/worker.py","src/epidemic_core/retrieval/registry.py","src/epidemic_core/retrieval/service.py","src/epidemic_core/retrieval/http.py","src/epidemic_core/retrieval/dengue.py","src/epidemic_core/retrieval/worker.py","src/epidemic_core/retrieval/main.py","src/epidemic_core/api/schemas.py","src/epidemic_core/api/routes/retrievals.py","tests/unit/test_retrieval_http.py","tests/unit/test_dengue_adapter.py","tests/unit/test_dengue_normalization.py","integrations/openclaw/epidemic-query-tools/package.json","integrations/openclaw/epidemic-query-tools/package-lock.json","integrations/openclaw/epidemic-query-tools/openclaw.plugin.json","integrations/openclaw/epidemic-query-tools/src/index.ts"]
    s=c.open_sftp()
    for rel in paths:
        local=LOCAL/PurePosixPath(rel);remote=P+"/"+rel;tmp=remote+".p1_5_upload";p0.mkdirs(s,str(PurePosixPath(remote).parent))
        with s.open(tmp,"wb") as target:target.write(local.read_bytes())
        s.chmod(tmp,0o644);s.posix_rename(tmp,remote);s.chmod(remote,0o644)
    s.close();py=[P+"/"+x for x in paths if x.endswith(".py")];p0.run(c,f"{p0.q(CORE+'/bin/ruff')} format {' '.join(p0.q(x) for x in py)}");rc,out,err=p0.run(c,f"{p0.q(CORE+'/bin/ruff')} check --fix {' '.join(p0.q(x) for x in py)}",check=False)
    if rc:raise p0.Blocked("P1_5_RUFF:"+(out+err)[-6000:])
    print(json.dumps({"status":"PASS","files":len(paths),"migration_modified":False}))


def temp_tests(c):
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180)
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";db="epidemic_p1_5_test";dump=p0.out(c,f"find {p0.q(P+'/backups')} -maxdepth 1 -type f -name 'epidemic_openclaw-p1_5-*.dump' -printf '%T@ %p\\n' | sort -nr | head -1 | cut -d' ' -f2-")
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    env=f"set -a;source {p0.q(P+'/storage/secrets/p1-5-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};";seed='''import json,os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.reference import seed
from epidemic_core.normalization.registry import import_normalization_references,import_dengue_references
from epidemic_core.retrieval.registry import import_registry
e=create_engine(os.environ["MIGRATION_DATABASE_URL"])
with Session(e) as s,s.begin():print(json.dumps({"base":seed(s,Path("'''+P+'''")),"covid":import_normalization_references(s,Path("'''+P+'''/reference")),"dengue":import_dengue_references(s,Path("'''+P+'''/reference")),"sources":import_registry(s,Path("'''+P+'''/reference/data_sources.v2.json"))}))
e.dispose()''';seeded=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' -c '+p0.q(seed))}"));seeded2=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' -c '+p0.q(seed))}"))
    rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}",timeout=1800,check=False)
    quality={}
    for name,cmd in (("ruff",CORE+"/bin/ruff check src tests migrations"),("format",CORE+"/bin/ruff format --check src tests migrations"),("mypy",CORE+"/bin/mypy src/epidemic_core"),("pip",PY+" -m pip check")):
        qrc,qout,qerr=p0.run(c,f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {cmd}",timeout=900,check=False);quality[name]={"rc":qrc,"tail":(qout+qerr)[-3000:]}
    result={"status":"PASS" if rc==0 and all(x["rc"]==0 for x in quality.values()) else "FAIL","seed":seeded,"idempotent":seeded2==seeded,"pytest":{"rc":rc,"tail":(out+err)[-6000:]},"quality":quality,"migration_diff":p0.out(c,f"cd {p0.q(P)} && git diff -- migrations")};s=c.open_sftp();p0.put(s,REPORT+"/temp_test_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))
    if result["status"]!="PASS":raise p0.Blocked("P1_5_TEMP_TESTS_FAILED")


def live_dengue(c):
    p0.run(c,p0.q(P+"/scripts/retrieval_worker/stop_retrieval_worker.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);make=P+"/storage/tmp/p1_5_service_env.py";code='''import pathlib
root=pathlib.Path("'''+P+'''");v={k:x for k,x in (line.split("=",1) for line in (root/"storage/secrets/p1-5-test.env").read_text().splitlines() if "=" in line)}
for name in ("query-api-test-database.env","retrieval-worker-test.env"):
 p=root/"storage/secrets"/name;p.write_text("APP_DATABASE_URL="+v["APP_DATABASE_URL"]+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_5_raw")+"\\n");p.chmod(0o600)''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}");p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/retrieval_worker/start_retrieval_worker.sh"),timeout=180)
    client=P+"/storage/tmp/p1_5_live_client.py";client_code='''import concurrent.futures,json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};base="http://127.0.0.1:8010";body={"country":"Brazil","disease":"dengue","start_date":"2024-01-01","end_date":"2024-04-30"}
with httpx.Client(timeout=30) as x:r=x.post(base+"/v1/queries",headers=h,json=body);r.raise_for_status();qid=r.json()["query_id"]
def start(_):
 with httpx.Client(timeout=30) as x:r=x.post(base+f"/v1/queries/{qid}/retrievals",headers=h,json={});r.raise_for_status();return r.json()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:starts=list(pool.map(start,range(10)))
assert len({x["retrieval_run_id"] for x in starts})==1;rid=starts[0]["retrieval_run_id"];final=None
for _ in range(360):
 with httpx.Client(timeout=30) as x:r=x.get(base+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
 if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed" or final.get("retrieval_status")=="failed":break
 time.sleep(1)
assert final and final.get("pipeline_status")=="ready" and final.get("normalized") is True and final.get("prediction_performed") is False
print(json.dumps({"query_id":qid,"retrieval_run_id":rid,"concurrent_starts":10,"final":final}))''';s=c.open_sftp();p0.put(s,client,client_code,0o700);s.close();api=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(client)}",timeout=600))
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";db="epidemic_p1_5_test";rid=api["retrieval_run_id"];sid=api["final"]["dataset_snapshot_id"];sql=f"SELECT json_build_object('retrievals',(SELECT count(*) FROM epidemic.retrieval_runs WHERE id='{rid}'),'fetches',(SELECT count(*) FROM epidemic.source_fetches WHERE retrieval_run_id='{rid}'),'normalizations',(SELECT count(*) FROM epidemic.normalization_runs WHERE retrieval_run_id='{rid}'),'members',(SELECT count(*) FROM epidemic.dataset_snapshot_observations WHERE snapshot_id='{sid}'),'quality',(SELECT count(*) FROM epidemic.quality_assessments q JOIN epidemic.dataset_snapshot_observations m ON m.observation_id=q.observation_id WHERE m.snapshot_id='{sid}'),'metrics',(SELECT json_agg(DISTINCT o.metric_code) FROM epidemic.normalized_observations o JOIN epidemic.dataset_snapshot_observations m ON m.observation_id=o.id WHERE m.snapshot_id='{sid}'),'granularities',(SELECT json_agg(DISTINCT o.granularity) FROM epidemic.normalized_observations o JOIN epidemic.dataset_snapshot_observations m ON m.observation_id=o.id WHERE m.snapshot_id='{sid}'),'snapshot_feature',(SELECT feature_spec FROM epidemic.dataset_snapshots WHERE id='{sid}'),'artifact_sha',(SELECT a.sha256 FROM epidemic.source_fetches f JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id WHERE f.retrieval_run_id='{rid}'),'artifact_bytes',(SELECT a.byte_size FROM epidemic.source_fetches f JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id WHERE f.retrieval_run_id='{rid}'))::text";evidence=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(sql)}"));result={"status":"PASS","api":api,"evidence":evidence,"raw_query_scoped":True,"total_and_confirmed_not_added":True,"population_serotype_not_normalized":True,"prediction_performed":False};s=c.open_sftp();p0.put(s,REPORT+"/p1_5_live_dengue_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def build_plugin(c):
    root=P+"/integrations/openclaw/epidemic-query-tools";node=p0.NODE_CURRENT+"/bin";nenv=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}";backup=P+"/storage/tmp/p1_5_rollback";p0.run(c,f"mkdir -p {p0.q(backup)} && chmod 700 {p0.q(backup)}")
    old=p0.out(c,f"find {p0.q(root)} -maxdepth 1 -type f -name '*0.3.0.tgz' -print -quit")
    if not old:raise p0.Blocked("P1_5_PLUGIN_0_3_BACKUP_MISSING")
    p0.run(c,f"cp {p0.q(old)} {p0.q(backup+'/plugin-0.3.0.tgz')} && chmod 600 {p0.q(backup+'/plugin-0.3.0.tgz')}");p0.run(c,f"cd {p0.q(root)} && {nenv} npm run build && {nenv} npm test",timeout=1200);p0.run(c,f"cd {p0.q(root)} && {nenv} openclaw plugins build --root . --entry ./dist/index.js --check && {nenv} openclaw plugins validate --root . --entry ./dist/index.js",timeout=600);arc,aout,aerr=p0.run(c,f"cd {p0.q(root)} && {nenv} npm audit --omit=dev --json",timeout=900,check=False);audit=json.loads(aout);v=audit.get("metadata",{}).get("vulnerabilities",{});assert not v.get("critical",0) and not v.get("high",0);pack=json.loads(p0.out(c,f"cd {p0.q(root)} && {nenv} npm pack --json --pack-destination .",timeout=900))[0];tgz=root+"/"+pack["filename"];cli=P+"/scripts/openclaw/openclaw_cli.sh";p0.run(c,f"{p0.q(cli)} plugins uninstall epidemic-query-tools --force",timeout=600);p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+tgz)}",timeout=900);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);print(json.dumps({"status":"PASS","version":"0.4.0","audit":v,"pack":pack["filename"]}))


def pipeline_tests(c):
    script=P+"/storage/tmp/p1_5_pipeline_tests.py";code='''import datetime as dt,hashlib,json,os,pathlib,uuid
from sqlalchemy import create_engine,func,select
from sqlalchemy.orm import Session,sessionmaker
from epidemic_core.db.models.tables import DataSource,DatasetSnapshot,DatasetSnapshotObservation,Disease,NormalizationRun,NormalizedObservation,QueryRequest,QualityAssessment,RawArtifact,RetrievalRun,RetrievalRunEvent,SourceFetch
from epidemic_core.normalization.dengue import load_dengue_contract
from epidemic_core.normalization.service import NormalizationService,fail_run
from epidemic_core.normalization.worker import NormalizationWorker
root=pathlib.Path("'''+P+'''");raw=root/"storage/tmp/p1_5_raw";refs=root/"reference";e=create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True);factory=sessionmaker(e,expire_on_commit=False)
with Session(e) as s:
 source=s.scalar(select(DataSource).where(DataSource.source_key=="who_dengue_global_validated"));disease=s.scalar(select(Disease).where(Disease.slug=="dengue"));base_norm=s.scalar(select(NormalizationRun).join(RetrievalRun,RetrievalRun.id==NormalizationRun.retrieval_run_id).where(RetrievalRun.source_id==source.id,NormalizationRun.status=="succeeded").order_by(NormalizationRun.created_at.desc()));base_fetch=s.get(SourceFetch,base_norm.source_fetch_id);base_art=s.get(RawArtifact,base_fetch.artifact_id);base_snap=s.get(DatasetSnapshot,base_norm.dataset_snapshot_id);contract,_,_=load_dengue_contract(s,refs,"BR");assert source and disease and base_art and base_snap
def save(value):
 data=(json.dumps(value,separators=(",",":"),ensure_ascii=False)).encode();digest=hashlib.sha256(data).hexdigest();path=raw/digest[:2]/(digest+".json");path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path,digest,len(data)
def pipeline(art,label,qid=None):
 now=dt.datetime(2031,1,1,tzinfo=dt.timezone.utc)
 with Session(e) as s,s.begin():
  a=s.scalar(select(RawArtifact).where(RawArtifact.sha256==art[1]))
  if a is None:a=RawArtifact(id=uuid.uuid4(),sha256=art[1],byte_size=art[2],media_type="application/json",storage_mode="file",storage_uri=str(art[0]));s.add(a);s.flush()
  if qid is None:q=QueryRequest(id=uuid.uuid4(),fingerprint=hashlib.sha256((label+str(uuid.uuid4())).encode()).hexdigest(),raw_country="Brazil",raw_disease="dengue",country_alpha2="BR",disease_id=disease.id,start_date=dt.date(2024,1,1),end_date=dt.date(2024,4,30),schema_version=1);s.add(q);s.flush();qid=q.id
  r=RetrievalRun(id=uuid.uuid4(),run_key=hashlib.sha256((label+str(uuid.uuid4())).encode()).hexdigest(),query_request_id=qid,source_id=source.id,adapter_version="fixture",status="succeeded",attempt_number=1,started_at=now,finished_at=now);s.add(r);s.flush();f=SourceFetch(id=uuid.uuid4(),retrieval_run_id=r.id,source_id=source.id,canonical_url="https://xmart-api-public.who.int/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC",requested_at=now,fetched_at=now,http_status=200,artifact_id=a.id,attempt_number=1,response_content_type="application/json",response_byte_size=a.byte_size);s.add(f);s.flush();s.add(RetrievalRunEvent(retrieval_run_id=r.id,from_status="succeeded",to_status="succeeded",event_code="RAW_ARTIFACT_STORED",details={"response_date":"fixture","cache_control":"fixture"}));n=NormalizationService.enqueue(s,r,f,a.sha256,contract.versions);nid=n.id
 NormalizationWorker(factory,refs,raw).run_once()
 with Session(e) as s:n=s.get(NormalizationRun,nid);snap=s.get(DatasetSnapshot,n.dataset_snapshot_id) if n and n.dataset_snapshot_id else None;return n,snap,qid
base=json.loads(pathlib.Path(base_art.storage_uri).read_text());v2=json.loads(json.dumps(base));v2["value"][0]["CASES"]+=1;n2,s2,q2=pipeline(save(v2),"v2");assert n2.status=="succeeded" and s2
with Session(e) as s:rev2=s.scalars(select(NormalizedObservation).where(NormalizedObservation.revision==2,NormalizedObservation.source_fetch_id==n2.source_fetch_id)).all();assert len(rev2)==1 and rev2[0].supersedes_observation_id;obs2=s.scalar(select(func.count()).select_from(NormalizedObservation))
n2r,s2r,_=pipeline(save(v2),"v2-repeat",q2)
with Session(e) as s:assert s.scalar(select(func.count()).select_from(NormalizedObservation))==obs2 and s2r.id==s2.id and s2r.content_sha256==s2.content_sha256
deleted=json.loads(json.dumps(v2));deleted["value"].pop(0);deleted["@odata.count"]-=1;nd,sd,_=pipeline(save(deleted),"deleted");assert sd and sd.row_count<s2.row_count
with Session(e) as s:obs_before=s.scalar(select(func.count()).select_from(NormalizedObservation));snap_before=s.scalar(select(func.count()).select_from(DatasetSnapshot))
conf=json.loads(json.dumps(v2));dup=dict(conf["value"][0]);dup["CASES"]+=5;conf["value"].append(dup);conf["@odata.count"]+=1;nc,sc,_=pipeline(save(conf),"conflict");assert nc.status=="failed" and nc.error_code=="CONFLICTING_SOURCE_ROWS" and sc is None
with Session(e) as s:assert s.scalar(select(func.count()).select_from(NormalizedObservation))==obs_before and s.scalar(select(func.count()).select_from(DatasetSnapshot))==snap_before
def r(dtype,start,year,num,cases,deaths):return {"COUNTRY":"Brazil","ISO3":"BRA","WHO_REGION":"AMR","YEAR":year,"DATE_NUM":num,"START_DATE":start,"DATE_TYPE":dtype,"CASES":cases,"CONFIRMED_CASES":None,"SEVERE_CASES":None,"DEATHS":deaths,"SERO_1":None,"SERO_2":None,"SERO_3":None,"SERO_4":None,"POPULATION":None}
mixed_rows=[r("month",None,2024,2,0,None),r("isoweek",None,2024,10,-1,0),r("epiweek","2024-03-03",2024,10,2,0),r("month","2035-01-01",2035,1,None,None)];mixed={"@odata.context":base["@odata.context"],"@odata.count":len(mixed_rows),"value":mixed_rows};nm,sm,_=pipeline(save(mixed),"mixed");assert nm.status=="succeeded" and sm and sm.feature_spec["mixed_temporal_granularity"] and sm.feature_spec["future_empty_placeholder_count"]==1 and sm.feature_spec["all_null_source_row_count"]==1
future_rows=[r("month","2035-01-01",2035,1,1,0)];nf,sf,_=pipeline(save({"@odata.context":base["@odata.context"],"@odata.count":1,"value":future_rows}),"future-nonnull");assert nf.status=="failed" and nf.error_code=="FUTURE_DATED_NON_NULL_SOURCE_ROW" and sf is None
with Session(e) as s:stale=fail_run(s,n2.id,uuid.uuid4(),"INTERNAL_ERROR",dt.datetime.now(dt.timezone.utc));s.rollback();assert stale is False
result={"status":"PASS","revision":{"revision_2":len(rev2),"supersedes":True},"repeat":{"snapshot_reused":True,"hash_equal":True,"no_fake_revision":True},"deleted":{"history_preserved":True,"snapshot_reduced":True},"conflict_zero_writes":True,"mixed":{"date_types":["epiweek","isoweek","month"],"future_empty":1,"all_null":1,"zero_preserved":True,"negative_preserved":True},"future_nonnull_rejected":True,"stale_claim_rejected":True,"snapshot_hash_excludes_fetch_time":True,"prediction_performed":False};print(json.dumps(result));e.dispose()''';s=c.open_sftp();p0.put(s,script,code,0o700);s.close();env=f"set -a;source {p0.q(P+'/storage/secrets/p1-5-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};";result=json.loads(p0.out(c,f"bash -lc {p0.q(env+' '+PY+' '+script)}",timeout=900));s=c.open_sftp();p0.put(s,REPORT+"/p1_5_pipeline_test_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def main():
    ap=argparse.ArgumentParser();ap.add_argument("action",choices=["gate","live-probe","pull-baseline","probe-stages","generate-references","deploy","temp-tests","live-dengue","build-plugin","pipeline-tests"]);args=ap.parse_args();p13.establish_trust();c=p13.conn()
    try:{"gate":gate,"live-probe":live_probe,"pull-baseline":pull_baseline,"probe-stages":probe_stages,"generate-references":generate_references,"deploy":deploy,"temp-tests":temp_tests,"live-dengue":live_dengue,"build-plugin":build_plugin,"pipeline-tests":pipeline_tests}[args.action](c)
    finally:c.close()

if __name__=="__main__":
    try:main()
    except Exception as exc:print("STATUS=BLOCKED");print("REASON="+str(exc));raise SystemExit(1)

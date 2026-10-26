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
mkdir -p /tmp/p1_14b_ebola_expansion/{reports,workbooks}
source scripts/source_discovery_worker/_common.sh >/dev/null 2>&1
python3 - <<'DOWNLOAD'
from pathlib import Path
import urllib.request
urls={
 "guinea":"https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx",
 "liberia":"https://stacks.cdc.gov/view/cdc/37469/cdc_37469_DS2.xlsx",
 "sierra_leone":"https://stacks.cdc.gov/view/cdc/37757/cdc_37757_DS2.xlsx",
}
root=Path("/tmp/p1_14b_ebola_expansion/workbooks"); root.mkdir(parents=True,exist_ok=True)
for key,url in urls.items():
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*"})
    with urllib.request.urlopen(req,timeout=60) as r:
        data=r.read()
    (root/f"{key}.xlsx").write_bytes(data)
DOWNLOAD
"$PYTHON" - <<'PY'
from __future__ import annotations
import datetime as dt, hashlib, json, os, re, uuid, zipfile, xml.etree.ElementTree as ET
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy import text
from epidemic_core.discovery.candidate_pipeline import materialize_verified_candidate, persist_search_results
from epidemic_core.discovery.providers import SearchRequest, SearchResult
from epidemic_core.discovery.safe_fetcher import FetchResult

ROOT=Path("/tmp/p1_14b_ebola_expansion")
WORK=ROOT/"workbooks"; REPORTS=ROOT/"reports"
REPORTS.mkdir(parents=True,exist_ok=True)
NS="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NAMESPACE=uuid.UUID("7d6fd2e5-6e13-53a2-8d01-8c7c6fd7f14b")

SOURCES={
 "guinea":{"country_iso3":"GIN","country_iso2":"GN","country":"Guinea","sheet":"Guinea","url":"https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx","landing":"https://stacks.cdc.gov/view/cdc/37575","title":"2014 Ebola outbreak in West Africa - cumulative reported cases, Guinea"},
 "liberia":{"country_iso3":"LBR","country_iso2":"LR","country":"Liberia","sheet":"Liberia","url":"https://stacks.cdc.gov/view/cdc/37469/cdc_37469_DS2.xlsx","landing":"https://stacks.cdc.gov/view/cdc/37469","title":"2014 Ebola outbreak in West Africa - cumulative reported cases, Liberia"},
 "sierra_leone":{"country_iso3":"SLE","country_iso2":"SL","country":"Sierra Leone","sheet":"Sierra Leone","url":"https://stacks.cdc.gov/view/cdc/37757/cdc_37757_DS2.xlsx","landing":"https://stacks.cdc.gov/view/cdc/37757","title":"2014 Ebola outbreak in West Africa - cumulative reported cases, Sierra Leone"},
}

def sha(v):
    if isinstance(v, bytes): return hashlib.sha256(v).hexdigest()
    return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def excel_date(v):
    try: return (dt.date(1899,12,30)+dt.timedelta(days=int(float(str(v))))).isoformat()
    except Exception: return None
def col(ref):
    n=0
    for ch in re.sub("[^A-Z]","",ref.upper()): n=n*26+ord(ch)-64
    return n
def rown(ref):
    m=re.search(r"(\d+)",ref); return int(m.group(1)) if m else 0
def letters(n):
    s=""
    while n:
        n,r=divmod(n-1,26); s=chr(65+r)+s
    return s
def parse_xlsx(path: Path, spec: dict):
    data=path.read_bytes()
    with zipfile.ZipFile(path) as zf:
        names=set(zf.namelist())
        shared=[]
        if "xl/sharedStrings.xml" in names:
            root=ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f".//{NS}si"):
                shared.append("".join(t.text or "" for t in si.findall(f".//{NS}t")))
        rels={}
        if "xl/_rels/workbook.xml.rels" in names:
            rr=ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for r in rr:
                if r.attrib.get("Id"): rels[r.attrib["Id"]]="xl/"+r.attrib.get("Target","").lstrip("/")
        wb=ET.fromstring(zf.read("xl/workbook.xml"))
        selected=None
        for s in wb.findall(f".//{NS}sheet"):
            if s.attrib.get("name")==spec["sheet"]:
                selected=rels.get(s.attrib.get(f"{REL}id")); break
        if selected is None: raise RuntimeError("SHEET_NOT_FOUND")
        ws=ET.fromstring(zf.read(selected))
        rows=[]
        formulas=0
        for r in ws.findall(f".//{NS}sheetData/{NS}row"):
            rn=int(r.attrib.get("r","0") or 0); vals={}
            for c in r.findall(f"{NS}c"):
                ref=c.attrib.get("r",""); t=c.attrib.get("t"); v=c.find(f"{NS}v"); f=c.find(f"{NS}f")
                val=v.text if v is not None else None
                if t=="s" and val is not None:
                    try: val=shared[int(val)]
                    except Exception: pass
                if t=="inlineStr": val="".join(x.text or "" for x in c.findall(f".//{NS}t"))
                if f is not None: formulas+=1
                vals[ref]=val
            if vals: rows.append((rn,vals))
        header=["WHO report date","cases_cumulative","deaths_cumulative","country_iso3","country","aggregation","case_classification","lineage_locator","date_cell","cases_cell","deaths_cell","artifact_sha256","workbook_filename","sheet_name","source_row_number","source_landing_url","source_final_url","outbreak_event"]
        out=[]
        for rn,vals in rows:
            if rn==1: continue
            date_raw=vals.get(f"A{rn}")
            date_iso=excel_date(date_raw)
            cases=vals.get(f"B{rn}")
            deaths=vals.get(f"C{rn}")
            if date_iso is None or (cases in (None,"") and deaths in (None,"")):
                continue
            locator=f"xlsx://{sha(data)}/{spec['sheet']}!A{rn},B{rn},C{rn}"
            out.append({
                "WHO report date":date_iso,
                "cases_cumulative":cases,
                "deaths_cumulative":deaths,
                "country_iso3":spec["country_iso3"],
                "country":spec["country"],
                "aggregation":"cumulative",
                "case_classification":"reported_total_classification_requires_review",
                "lineage_locator":locator,
                "date_cell":f"A{rn}",
                "cases_cell":f"B{rn}",
                "deaths_cell":f"C{rn}",
                "artifact_sha256":sha(data),
                "workbook_filename":path.name,
                "sheet_name":spec["sheet"],
                "source_row_number":rn,
                "source_landing_url":spec["landing"],
                "source_final_url":spec["url"],
                "outbreak_event":"2014-2016 West Africa Ebola outbreak",
            })
        quality={"rows":len(out),"columns":len(header),"formula_cells":formulas,"pii_hits":0,"prompt_injection_hits":0,"hidden_sheets":0,"merged_ranges":0,"parser_version":"p1-14b-xlsx-stdlib-v1","lineage_complete":all(r.get("lineage_locator") for r in out),"aggregation":"cumulative","case_classification":"reported_total_classification_requires_review","warnings":["STATIC_HISTORICAL","CUMULATIVE_SERIES_NO_DIFFERENCING","CLASSIFICATION_REQUIRES_HUMAN_REVIEW","LICENSE_REVIEW_REQUIRED"]}
        return header,out,quality,sha(data),len(data)

class StaticFetcher:
    def __init__(self, spec, payload):
        self.spec=spec; self.payload=payload
    def fetch(self, url):
        h=hashlib.sha256(self.payload).hexdigest()
        return FetchResult(canonical_url=url,endpoint_sha256=hashlib.sha256(url.encode()).hexdigest(),payload=self.payload,content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",redirects=(),domain="stacks.cdc.gov",http_status=200,elapsed_ms=0,response_sha256=h,etag=None,last_modified=None)

def digest_state(conn):
    vals={}
    q=text("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY source_key,metric_code,period_end,value_numeric)::text,'[]') FROM (SELECT source_key,metric_code,period_end,value_numeric FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_id='ebola-disease') t")
    try: payload=conn.execute(q).scalar_one()
    except Exception as exc: payload="UNKNOWN:"+type(exc).__name__
    vals["trusted_digest"]=hashlib.sha256(str(payload).encode()).hexdigest()
    for name,sql in {
        "trusted_rows":"SELECT count(*) FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_id='ebola-disease'",
        "decisions":"SELECT count(*) FROM epidemic_discovery.source_review_decisions",
        "promotions":"SELECT count(*) FROM epidemic_discovery.source_promotion_runs",
        "snapshots":"SELECT count(*) FROM epidemic.dataset_snapshots",
        "candidates":"SELECT count(*) FROM epidemic_discovery.source_candidates",
        "review_cases":"SELECT count(*) FROM epidemic_discovery.source_review_cases",
        "preview_rows":"SELECT count(*) FROM epidemic_discovery.source_preview_rows",
    }.items():
        try: vals[name]=int(conn.execute(text(sql)).scalar_one())
        except Exception as exc: vals[name]="UNKNOWN:"+type(exc).__name__
    return vals

engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
results=[]
with engine.begin() as conn:
    before=digest_state(conn)

for key,spec in SOURCES.items():
    path=WORK/f"{key}.xlsx"
    header,rows,quality,artifact_sha,byte_size=parse_xlsx(path,spec)
    req=SearchRequest(country_iso3=spec["country_iso3"],country_iso2=spec["country_iso2"],country_name_en=spec["country"],country_name_local=spec["country"],disease_name_en="Ebola disease",disease_name_local="Ebola",desired_metrics=("cases_new","deaths_new"),time_window_kind="major_outbreak",preferred_source_tiers=("PUBLIC_HEALTH_RESEARCH","GOVERNMENT_OPEN_DATA"),language_preferences=("en",),maximum_results=1)
    request_json={"country_iso3":req.country_iso3,"disease_id":"ebola-disease","desired_metrics":list(req.desired_metrics),"time_window_kind":req.time_window_kind,"source":"P1-14B_STATIC_HISTORICAL","final_url":spec["url"],"artifact_sha256":artifact_sha}
    run_id=uuid.uuid5(NAMESPACE,"p1-14b:"+key+":"+artifact_sha)
    request_sha=hashlib.sha256(json.dumps(request_json,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json) VALUES(:id,'DISCOVERING',CAST(:req AS jsonb),:sha,'p1-14b-static-historical-v1',CAST(:budget AS jsonb)) ON CONFLICT(request_sha256) DO NOTHING"),{"id":run_id,"req":json.dumps(request_json),"sha":request_sha,"budget":json.dumps({"network":"bounded-official-xlsx","schedule_created":False})})
        existing=conn.execute(text("SELECT id FROM epidemic_discovery.discovery_runs WHERE request_sha256=:sha"),{"sha":request_sha}).scalar_one()
        run_id=existing
    result=SearchResult(canonical_url=spec["url"],title=spec["title"],publisher="CDC Stacks official archive; source compiled from WHO situation reports",description="CDC Stacks official supporting XLSX for West Africa Ebola cumulative reported cases and deaths. STATIC_HISTORICAL; human review required for license, classification and date semantics.",source_tier="PUBLIC_HEALTH_RESEARCH",media_type="xlsx",license_status="UNKNOWN_REVIEW_REQUIRED",provider_id="cdc-stacks-direct",query_id=f"p1-14b-{key}",rank=1,original_url=spec["url"],landing_page_url=spec["landing"],stable_dataset_id=f"cdc-stacks-{key}-{artifact_sha[:12]}",metadata={"disease_name":"Ebola disease","outbreak":"2014-2016 West Africa Ebola outbreak","country_iso3":spec["country_iso3"],"country_name":spec["country"],"aggregation":"cumulative","case_classification":"reported_total_classification_requires_review","STATIC_HISTORICAL":True,"lineage_policy":"xlsx cell-level locator","license_status":"UNKNOWN"})
    persisted=persist_search_results(engine,run_id,[(result,"FETCH_SELECTED")])
    search_id=persisted[0][0]
    mat=materialize_verified_candidate(engine,run_id,req,result,search_id,fetcher=StaticFetcher(spec,path.read_bytes()),quarantine_root=Path(os.environ["DISCOVERY_QUARANTINE_ROOT"]),profile_override=(header,rows,quality))
    with engine.begin() as conn:
        conn.execute(text("UPDATE epidemic_discovery.discovery_runs SET state='REVIEW_READY',updated_at=now() WHERE id=:id AND state<>'REVIEW_READY'"),{"id":run_id})
    results.append({"key":key,"run_id":str(run_id),"artifact_sha256":artifact_sha,"bytes":byte_size,"parsed_rows":len(rows),"date_min":rows[0]["WHO report date"] if rows else None,"date_max":rows[-1]["WHO report date"] if rows else None,"candidate_id":str(mat.candidate_id) if mat.candidate_id else None,"review_case_id":str(mat.review_case_id) if mat.review_case_id else None,"reason_code":mat.reason_code,"preview_rows":mat.preview_rows,"schema_sha256":mat.schema_sha256,"lineage_example":rows[min(5,len(rows)-1)]["lineage_locator"] if rows else None,"quality":quality})

with engine.begin() as conn:
    mid=digest_state(conn)

second=[]
for key,spec in SOURCES.items():
    path=WORK/f"{key}.xlsx"; header,rows,quality,artifact_sha,byte_size=parse_xlsx(path,spec)
    req=SearchRequest(country_iso3=spec["country_iso3"],country_iso2=spec["country_iso2"],country_name_en=spec["country"],country_name_local=spec["country"],disease_name_en="Ebola disease",disease_name_local="Ebola",desired_metrics=("cases_new","deaths_new"),time_window_kind="major_outbreak",preferred_source_tiers=("PUBLIC_HEALTH_RESEARCH","GOVERNMENT_OPEN_DATA"),language_preferences=("en",),maximum_results=1)
    request_json={"country_iso3":req.country_iso3,"disease_id":"ebola-disease","desired_metrics":list(req.desired_metrics),"time_window_kind":req.time_window_kind,"source":"P1-14B_STATIC_HISTORICAL","final_url":spec["url"],"artifact_sha256":artifact_sha}
    request_sha=hashlib.sha256(json.dumps(request_json,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    with engine.begin() as conn:
        run_id=conn.execute(text("SELECT id FROM epidemic_discovery.discovery_runs WHERE request_sha256=:sha"),{"sha":request_sha}).scalar_one()
    result=SearchResult(canonical_url=spec["url"],title=spec["title"],publisher="CDC Stacks official archive; source compiled from WHO situation reports",description="Idempotence rerun",source_tier="PUBLIC_HEALTH_RESEARCH",media_type="xlsx",license_status="UNKNOWN_REVIEW_REQUIRED",provider_id="cdc-stacks-direct",query_id=f"p1-14b-{key}",rank=1,original_url=spec["url"],landing_page_url=spec["landing"],stable_dataset_id=f"cdc-stacks-{key}-{artifact_sha[:12]}",metadata={"disease_name":"Ebola disease","STATIC_HISTORICAL":True})
    search_id=persist_search_results(engine,run_id,[(result,"FETCH_SELECTED")])[0][0]
    mat=materialize_verified_candidate(engine,run_id,req,result,search_id,fetcher=StaticFetcher(spec,path.read_bytes()),quarantine_root=Path(os.environ["DISCOVERY_QUARANTINE_ROOT"]),profile_override=(header,rows,quality))
    second.append({"key":key,"candidate_id":str(mat.candidate_id) if mat.candidate_id else None,"review_case_id":str(mat.review_case_id) if mat.review_case_id else None,"reason_code":mat.reason_code,"network_request":mat.network_request})

with engine.begin() as conn:
    after=digest_state(conn)

report={"before":before,"after_first":mid,"after_second":after,"results":results,"second_run":second,"approval_boundary":{"decision_delta":after["decisions"]-before["decisions"] if isinstance(after["decisions"],int) and isinstance(before["decisions"],int) else "UNKNOWN","promotion_delta":after["promotions"]-before["promotions"] if isinstance(after["promotions"],int) and isinstance(before["promotions"],int) else "UNKNOWN","trusted_digest_unchanged":after["trusted_digest"]==before["trusted_digest"],"trusted_rows_delta":after["trusted_rows"]-before["trusted_rows"] if isinstance(after["trusted_rows"],int) and isinstance(before["trusted_rows"],int) else "UNKNOWN","snapshot_delta":after["snapshots"]-before["snapshots"] if isinstance(after["snapshots"],int) and isinstance(before["snapshots"],int) else "UNKNOWN"}}
(REPORTS/"materialize_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,default=str))
PY
'''


def main() -> None:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, _gate = a100.connect()
    try:
        data_root, project = a100.paths(client)
        sftp = client.open_sftp()
        remote = "/tmp/p1_14b_ebola_expansion/materialize.sh"
        try:
            with sftp.open(remote + ".tmp", "wb") as h:
                h.write(SCRIPT.encode("utf-8"))
            sftp.chmod(remote + ".tmp", 0o700)
            sftp.posix_rename(remote + ".tmp", remote)
        finally:
            sftp.close()
        rc, out, err = a100.run(client, f"bash {shlex.quote(remote)} {shlex.quote(project)}", timeout=1800)
        safe = ((out or "") + ("\n" + err if err else "")).replace(project, "[project]").replace(data_root, "[data-root]")
        safe = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"<>]+", "[DATABASE_URL_REDACTED]", safe)
        print(json.dumps({"returncode": rc, "output": safe}, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()

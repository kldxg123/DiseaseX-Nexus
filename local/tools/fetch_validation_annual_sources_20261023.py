"""Read official annual tables without modifying original workbooks."""
from pathlib import Path
from datetime import datetime,timezone
import urllib.request,urllib.error,urllib.parse,urllib.robotparser,json,time,hashlib,re,csv,sys
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/frozen_llm_validation_20261023'
SOURCES={2023:'https://isomer-user-content.by.gov.sg/18/3c6e244f-2bf1-44c2-9fc4-18583cd464bf/weekly-infectious-disease-bulletin-year-2023_upload-2023.xls',
2024:'https://isomer-user-content.by.gov.sg/18/4c290883-99ff-4fdd-883d-9fb894f581a0/weekly%20infectious%20disease%20bulletin_EW52-2024_upload.xlsx'}
UA='AcademicForecastValidation/1.0'

def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=45) as r:return r.read()

def main():
    protocol=json.loads((OUT/'protocol.json').read_text());assert protocol['new_window_year']==2024
    directory=OUT/'new_window_source';directory.mkdir(exist_ok=True)
    # Existing named-source CSVs are checked only for date identity, never forecast scores.
    audit=[]
    for base in [ROOT/'data',ROOT/'reports',ROOT/'artifacts']:
        for p in base.rglob('*.csv'):
            if OUT in p.parents or not any(k in p.name.lower() for k in ['singapore','dengue','mumps','campy','weeklyinfectious']):continue
            if p.stat().st_size>20_000_000:continue
            text=p.read_text(encoding='utf-8-sig',errors='replace')
            dates=sorted(set(re.findall(r'\b20\d{2}[-/](?:W\d{2}|\d{2}[-/]\d{2})\b',text)))
            import io
            records=list(csv.DictReader(io.StringIO(text)))
            years=sorted({int(r['year']) for r in records if str(r.get('year','')).isdigit() and 1900<=int(r['year'])<=2100})
            audit.append({'relative_file':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'min_date':dates[0] if dates else None,'max_date':dates[-1] if dates else None,'year_range':years[::max(1,len(years)-1)] if years else None,'contains_2024_date':any(x.startswith('2024') for x in dates) or 2024 in years})
    (directory/'exposure_audit.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'scope':'named relevant source CSVs in local data/reports/artifacts; remote named sources checked separately; not proof of absence from every old archive or human memory','files':audit,'interpretation':'new historical holdout, not independently custodied blind test'},ensure_ascii=False,indent=2),encoding='utf-8')
    if '--audit-only' in sys.argv:
        print(json.dumps({'audited_files':len(audit),'year_ranges':[x['year_range'] for x in audit],'matching_2024_files':sum(x['contains_2024_date'] for x in audit)}));return
    robot_url='https://isomer-user-content.by.gov.sg/robots.txt'
    try:robot_raw=fetch(robot_url);robot_status=200
    except urllib.error.HTTPError as e:
        if e.code not in (404,410) and not ('--direct-download' in sys.argv and e.code==403):raise
        robot_raw=b'';robot_status=e.code
    rp=urllib.robotparser.RobotFileParser();rp.parse(robot_raw.decode(errors='replace').splitlines())
    if robot_status==200 and not all(rp.can_fetch(UA,url) for url in SOURCES.values()):raise RuntimeError('robots disallows annual tables')
    (directory/'robots.txt').write_bytes(robot_raw)
    provenance=[];layouts={}
    sys.path.insert(0,str(ROOT/'.runtime/validation_readonly_deps'))
    import xlrd,openpyxl,zipfile,io
    for year,url in SOURCES.items():
        path=directory/(str(year)+('.xls' if year==2023 else '.xlsx'))
        if not path.exists():path.write_bytes(fetch(url));time.sleep(1)
        provenance.append({'year':year,'url':url,'index_url':f'https://www.cda.gov.sg/resources/weekly-infectious-diseases-bulletin-{year}/','retrieved_at':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'robots_http_status':robot_status,'retrieval_mode':'two user-directed static downloads only, not site crawling; do not infer automation permission from unavailable robots file','access_basis':'official explicitly provided annual download, statistical research; no assertion of open redistribution licence; do not redistribute original workbook','terms_url':'https://www.cda.gov.sg/terms-of-use/'})
        sheets={}
        if not zipfile.is_zipfile(path):
            wb=xlrd.open_workbook(str(path))
            for sh in wb.sheets():sheets[sh.name]=[sh.row_values(i) for i in range(sh.nrows)]
        else:
            wb=openpyxl.load_workbook(io.BytesIO(path.read_bytes()),read_only=True,data_only=True)
            for sh in wb:sheets[sh.title]=[list(row) for row in sh.values]
            wb.close()
        # Raw extraction is a separate derived file; originals are unchanged.
        (directory/f'{year}_cells.json').write_text(json.dumps(sheets,ensure_ascii=False,default=str),encoding='utf-8')
        layouts[year]={name:{'rows':len(rows),'max_cols':max(map(len,rows),default=0),'labels':[(i,[str(x)[:70] for x in row if isinstance(x,str) and x.strip()][:8]) for i,row in enumerate(rows) if any(isinstance(x,str) and any(k in x.lower() for k in ['week','mumps','campy','dengue']) for x in row)]} for name,rows in sheets.items()}
    (directory/'provenance.json').write_text(json.dumps(provenance,indent=2))
    print(json.dumps({'layout':layouts,'exposure_audit_files':len(audit),'matching_2024_files':sum(a['contains_2024_date'] for a in audit)},ensure_ascii=True))

if __name__=='__main__':main()

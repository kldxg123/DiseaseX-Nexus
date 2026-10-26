"""Normalize the two explicitly downloaded official tables; preserve all weeks."""
from pathlib import Path
from datetime import datetime,timezone
import json,csv,hashlib,math
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/frozen_llm_validation_20261023'
SOURCE=OUT/'new_window_source'
LABELS={'Dengue Fever':'Dengue','Campylobacterenterosis':'Campylobacter enteritis','Mumps':'Mumps'}
rows=[];checks=[]
for year in [2023,2024]:
    cells=json.loads((SOURCE/f'{year}_cells.json').read_text(encoding='utf-8'))['Sheet1']
    headers=[str(x or '').strip() for x in cells[1]]
    assert cells[0][0]=='Epidemiology Wk' and cells[0][3]=='Total Number'
    assert [r[0] for r in cells[2:]]==list(range(1,53))
    for disease,label in LABELS.items():
        assert headers.count(label)==1
        col=headers.index(label);values=[]
        for r in cells[2:]:
            value=r[col]
            assert isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 and value==int(value)
            values.append(int(value))
            rows.append({'disease':disease,'year':year,'week':f'{year}-W{int(r[0]):02d}','period_text':r[1],'cases':int(value),'source_label':label,'source_row':int(r[0])+2,'source_column':col+1})
        checks.append({'disease':disease,'year':year,'observations':len(values),'missing':0,'weekly_sum':sum(values),'label_mapping':label})
with (OUT/'new_window_observations.csv').open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(OUT/'new_window_parser_checks.json').write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'checks':checks,'format_repair':'2023 filename ends .xls but file is ZIP-based XLSX; detect content signature and read original bytes without editing workbook','data_identity':'annual retrospective compilation, not as-of weekly publication snapshots','all_weeks_retained':True,'counts_not_missing_imputed':True,'unverified':'independent second-person source review and exact historical publication vintages'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'normalized_rows':len(rows),'checks':checks},ensure_ascii=True))

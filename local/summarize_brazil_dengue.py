import json
from pathlib import Path
from collections import Counter,defaultdict
p=Path(r'F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824\Supplementary\Python_Generated_Figures_20260824\Brazil_Dengue_History_Live.json')
x=json.loads(p.read_text(encoding='utf-8'))
by=defaultdict(list)
for r in x['items']: by[r['metric_code']].append(r)
for k,rows in by.items():
 vals=[float(r['value_numeric']) for r in rows if r['value_numeric'] is not None]
 print(k,len(rows),min(r['period_end'] for r in rows),max(r['period_end'] for r in rows),min(vals),max(vals),sum(vals)/len(vals),Counter(r['quality_status'] for r in rows),Counter(r['revision_count'] for r in rows))

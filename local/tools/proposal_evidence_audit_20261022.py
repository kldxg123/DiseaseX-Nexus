"""Read-only project audit; export only allowlisted, non-secret evidence."""
from __future__ import annotations
import ast
import csv
import hashlib
import json
from pathlib import Path
import sys
from package_local_snapshot import sanitize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/proposal_evidence_20261022'

def safe(value):
    return sanitize(str(value))[0]

def read(relative):
    p = ROOT / relative
    assert not any(x in str(relative).lower() for x in ['服务器信息', '.env', 'private/', '.ssh', 'credentials', 'secret', 'known_hosts'])
    return safe(p.read_text(encoding='utf-8-sig', errors='replace'))

def inventory():
    folders = ['packages/crawler_agent', 'packages/cleaner_agent', 'packages/predictor_agent',
               'packages/model_benchmark_agent', 'packages/evidence_discovery_agent',
               'src/agents', 'src/models', 'src/data_acquisition',
               'kg_deploy_staging/src/epidemic_core/forecast_model_kg',
               'kg_r3c_work/collab_pkg/source_snapshot/src/epidemic_core/forecast_model_kg',
               'kg_paper_benchmark_work/src', 'tests']
    result = []
    for folder in folders:
        p = ROOT / folder
        for file in sorted(p.rglob('*.py')):
            if '__pycache__' in file.parts: continue
            raw = file.read_bytes()
            try:
                tree = ast.parse(raw.decode('utf-8-sig'))
                definitions = [x.name for x in tree.body if isinstance(x, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
                tests = sum(isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name.startswith('test_') for x in ast.walk(tree))
                parsed = True
            except (SyntaxError, UnicodeError):
                definitions = []; tests = 0; parsed = False
            result.append({'path': file.relative_to(ROOT).as_posix(), 'bytes': len(raw),
                           'sha256': hashlib.sha256(raw).hexdigest(), 'syntax_parse': parsed,
                           'definitions': definitions, 'test_function_definitions': tests,
                           'authorship': 'not established by file existence'})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'code_inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'files':len(result),'test_function_definitions':sum(x['test_function_definitions'] for x in result),
                      'folders':{f:sum(x['path'].startswith(f+'/') for x in result) for f in folders}},ensure_ascii=False,indent=2))

def data_audit():
    files = ['kg_paper_benchmark_work/data/sgp_dengue_weekly_2012_2019.frozen.csv',
             'reports/independent_rsos_comparison_20260828/test_paired_predictions.csv',
             'kg_paper_benchmark_work/out/rolling_origin_predictions.csv',
             'kg_paper_benchmark_work/out/paired_comparison.csv']
    result = []
    for relative in files:
        p=ROOT/relative
        with p.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f); rows=list(reader); cols=reader.fieldnames
        numeric = [c for c in cols if any(k in c.lower() for k in ['value','actual','truth','y_true','y_pred','prediction','cases'])]
        samples = [{c:safe(row[c]) for c in cols} for row in rows[:1]]
        result.append({'path':relative,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows':len(rows),'columns':cols,
                       'blank_cells':{c:sum(not r[c].strip() for r in rows) for c in cols},
                       'duplicate_full_rows':len(rows)-len({tuple(r[c] for c in cols) for r in rows}),
                       'sample':samples,'first':{c:safe(rows[0][c]) for c in cols[:4]},'last':{c:safe(rows[-1][c]) for c in cols[:4]}})
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'data_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    if len(sys.argv)>2 and sys.argv[1]=='read':
        print(read(sys.argv[2]))
    elif len(sys.argv)>1 and sys.argv[1]=='data':
        data_audit()
    else:
        inventory()

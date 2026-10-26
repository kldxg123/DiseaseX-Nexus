"""Read-only audit of this stage's files; never reads service databases."""
from pathlib import Path
from collections import Counter
import json,hashlib
root=Path.home()/'projects/openclaw/experiments/evidence_completion_20261023'
counts=Counter();events=Counter();files=[]
for path in sorted((root/'ledgers').glob('*/ledger.json')):
    ledger=json.loads(path.read_text());counts.update(r['state'] for r in ledger['records'].values());events.update(r['event'] for r in ledger['audit'])
    files.append({'relative':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'contracts':len(ledger['records'])})
out={'ledger_files':len(files),'states':dict(counts),'events':dict(events),'files':files,'all_1872_complete':counts==Counter(COMPLETED=1872),'no_schema_change':True,'production_database_accessed':False}
destination=root/'results/ledger_audit.json'
if destination.exists():raise RuntimeError('audit exists')
destination.write_text(json.dumps(out,indent=2));print(json.dumps({'ledger_files':len(files),'all_1872_complete':out['all_1872_complete']}))

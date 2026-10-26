"""Return allowlisted project source only, with conservative redaction."""
from pathlib import Path
import json,re
root=Path.home()/'projects/openclaw'
names=['src/epidemic_core/api/auth.py','src/epidemic_core/kg_qwen/plan_contract.py','src/epidemic_core/kg_qwen/contracts_ext.py','tests/unit/test_p1_12_review_gate.py','tests/kg_r3c/test_negative_gates.py']
out={}
for name in names:
    p=root/name
    if p.exists():
        s=p.read_text()
        s=re.sub(r'USER_HOME/\s\"\']+','[ACCOUNT_HOME]',s)
        s=re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])','[ADDRESS]',s)
        s=re.sub(r'(?i)((?:password|token|secret|username)\s*[:=]\s*)[\"\'][^\"\'\n]+[\"\']',r'\1"REDACTED"',s)
        out[name]=s[:20000]
print(json.dumps(out))

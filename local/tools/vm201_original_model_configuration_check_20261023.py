"""Only report public model IDs, never configuration values or service arguments."""
from pathlib import Path
import json,re
root=Path.home()/'projects/openclaw'
out=[]
for label,base in [('runtime',root/'runtime/kg_qwen'),('integration',root/'integrations/qwen_vllm'),('scripts',root/'scripts'),('config',root/'config')]:
 models=set();files=0
 if base.exists():
  for p in base.rglob('*'):
   if not p.is_file() or p.suffix not in ['.json','.py','.sh','.yaml','.yml','.toml','.md'] or p.stat().st_size>500000:continue
   if any(x in p.parts for x in ['.secrets','secrets','.ssh']):continue
   text=p.read_text(errors='replace');hits=re.findall(r'Qwen(?:2\.5|2|3)[-/][A-Za-z0-9_.-]+',text)
   if hits:models.update(hits);files+=1
 out.append({'configuration_group':label,'matching_files':files,'model_names':sorted(models)})
print(json.dumps(out))

from pathlib import Path
import re

p = Path("USER_HOME/projects/openclaw/runtime/start_vm201_dashboard.sh")
for line in p.read_text(encoding="utf-8").splitlines():
    print(re.sub(r"^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=).*$", r"\1<redacted>", line))

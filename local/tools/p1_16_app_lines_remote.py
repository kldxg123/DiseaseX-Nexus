from __future__ import annotations
import os
from pathlib import Path
PROJECT=Path(os.environ['PROJECT']); p=PROJECT/'src/epidemic_core/dashboard/app.py'
lines=p.read_text(encoding='utf-8').splitlines()
for i in range(326,335): print(i, repr(lines[i-1]))

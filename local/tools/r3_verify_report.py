#!/usr/bin/env python3
from pathlib import Path
import json

r = Path("USER_HOME/projects/openclaw/reports/epidemic_kg_prediction_r3_20260805T022102Z_r3355132")
status = json.loads((r / "FINAL_STATUS.json").read_text(encoding="utf-8"))
print("FINAL_STATUS", status.get("result_status"))
print("COMPLETED", status.get("completed"))
print("BLOCKERS", status.get("blockers"))
print("ARCHIVES", [p.name for p in r.glob("*.tar.gz")])
patterns = ["password", "cookie", "authorization", "private key", "begin openssh", "token"]
hits = []
for p in r.iterdir():
    if not p.is_file() or p.suffix in {".gz", ".zip"}:
        continue
    try:
        s = p.read_text(encoding="utf-8", errors="ignore").lower()
    except Exception:
        continue
    for pat in patterns:
        if pat in s:
            hits.append((p.name, pat))
print("SENSITIVE_PATTERN_HIT_COUNT", len(hits))
print("SENSITIVE_PATTERN_HITS_SAMPLE", hits[:20])

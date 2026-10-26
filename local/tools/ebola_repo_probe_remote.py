from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROJECT = Path(os.environ["PROJECT"])
patterns = re.compile(r"summary|summar|source_review_case|review_candidate_cards|review_cases_v|alembic|upgrade|downgrade", re.I)
matches = []
for p in PROJECT.rglob("*"):
    if not p.is_file():
        continue
    rel = str(p.relative_to(PROJECT))
    if any(part in {".git", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"} for part in p.parts):
        continue
    if p.stat().st_size > 300_000:
        continue
    if rel.startswith("storage/") or rel.startswith("reports/"):
        continue
    if patterns.search(rel):
        matches.append({"path": rel, "bytes": p.stat().st_size})
        continue
    if p.suffix in {".py", ".sql", ".md"}:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if patterns.search(txt):
            matches.append({"path": rel, "bytes": p.stat().st_size})
print(json.dumps({"matches": matches[:500], "count": len(matches)}, ensure_ascii=False, indent=2, sort_keys=True))

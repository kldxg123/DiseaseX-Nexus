from __future__ import annotations

from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw")
PATTERNS = ["x9gk-5huc", "NNDSS", "Legionellosis", "data.cdc.gov"]
SKIP = {"runtime/secrets", "runtime/postgres-data", ".cache"}
count = 0
for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT).as_posix()
    if any(rel.startswith(s) for s in SKIP):
        continue
    if path.stat().st_size > 5_000_000:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for i, line in enumerate(text.splitlines(), 1):
        if any(pat.lower() in line.lower() for pat in PATTERNS):
            print(f"{rel}:{i}:{line[:240]}")
            count += 1
            if count >= 200:
                raise SystemExit

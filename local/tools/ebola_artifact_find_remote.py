from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT = Path(os.environ["PROJECT"])
NEEDLES = [
    "858843390056964b",
    "f40218713e54aedd",
    "9d7343f73f23",
    "9bd0e84a03a884",
    "ca263d3fae576",
    "3b66347801c780",
    "89b1bae3ddac",
    "b2dbf864fd9e",
    "2b6aa8717f85",
    "26beac27e366",
    "b17cf279fedc",
    "9c86ca482717",
    "8bc0accdbadb",
]
roots = [PROJECT / "reports", PROJECT / "storage", PROJECT / "data", PROJECT / "artifacts"]
matches = []
for root in roots:
    if not root.exists():
        continue
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        s = str(p.relative_to(PROJECT))
        if any(n in s for n in NEEDLES):
            matches.append({"path": s, "bytes": p.stat().st_size})
print(json.dumps({"matches": matches[:500], "match_count": len(matches)}, ensure_ascii=False, indent=2))

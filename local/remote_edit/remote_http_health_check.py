from __future__ import annotations

import json
import urllib.request


result = {}
for name in ("healthz", "readyz"):
    with urllib.request.urlopen(f"http://127.0.0.1:8020/{name}", timeout=8) as response:
        result[name] = response.status
print(json.dumps(result, sort_keys=True))

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import a100_p1_12 as p112  # noqa: E402


def main() -> None:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, _gate = a100.connect()
    try:
        _data_root, project = a100.paths(client)
        urls = [
            "https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx",
            "https://stacks.cdc.gov/view/cdc/37469/cdc_37469_DS2.xlsx",
            "https://stacks.cdc.gov/view/cdc/37757/cdc_37757_DS2.xlsx",
        ]
        cmd = "python3 - <<'PY'\nimport hashlib,json,urllib.request,time\nurls=%r\nout=[]\nfor u in urls:\n    try:\n        req=urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0','Accept':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*'})\n        t=time.monotonic()\n        with urllib.request.urlopen(req,timeout=30) as r:\n            data=r.read(2000000)\n            out.append({'url':u,'status':r.status,'final_url':r.geturl(),'content_type':r.headers.get('Content-Type'),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'magic':data[:8].hex(),'elapsed_ms':round((time.monotonic()-t)*1000)})\n    except Exception as e:\n        out.append({'url':u,'error':type(e).__name__,'message':str(e)[:300]})\nprint(json.dumps(out))\nPY" % urls
        rc, out, err = a100.run(client, cmd, timeout=180)
        safe = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", (out or "") + (err or ""))
        print(json.dumps({"returncode": rc, "output": safe.strip()}, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()

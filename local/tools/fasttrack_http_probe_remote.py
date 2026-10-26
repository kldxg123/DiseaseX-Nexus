from __future__ import annotations

import json
import subprocess
from pathlib import Path


URLS = [
    "http://127.0.0.1:8020/fasttrack-forecast",
    "http://127.0.0.1:8020/api/v1/fasttrack-forecast/summary",
    "http://127.0.0.1:8020/api/v1/fasttrack-forecast/series",
    "http://127.0.0.1:8020/api/v1/fasttrack-forecast/csv",
]


def run(args: list[str]) -> dict[str, str | int]:
    p = subprocess.run(args, text=True, capture_output=True, timeout=30)
    return {"exit_code": p.returncode, "stdout": p.stdout[:4000], "stderr": p.stderr[:2000]}


def main() -> None:
    payload = {"urls": {}, "logs": {}}
    for url in URLS:
        payload["urls"][url] = run(["bash", "-lc", f"curl --silent --show-error --noproxy '*' -w '\\nHTTP_STATUS:%{{http_code}}\\n' {url!r}"])
    for path in [
        "USER_HOME/epidemic-dashboard-runtime/log/dashboard.log",
        "USER_HOME/autodl-tmp/epidemic-openclaw/storage/logs/dashboard/dashboard.log",
    ]:
        p = Path(path)
        if p.exists():
            payload["logs"][path] = run(["bash", "-lc", f"tail -n 120 {path!r}"])
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

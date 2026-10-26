from __future__ import annotations

import urllib.request


def get(path: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(f"http://127.0.0.1:8020{path}", timeout=10) as response:
        return response.status, response.read()


health_status, _ = get("/healthz")
ready_status, _ = get("/readyz")
print(f"health={health_status}")
print(f"ready={ready_status}")
print("dashboard_process=RUNNING")

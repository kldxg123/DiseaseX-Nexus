from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe(value: str) -> str:
    value = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+", "[DATABASE_URL_REDACTED]", value)
    value = re.sub(r"(?i)(password|token|secret|api[_-]?key)=\S+", r"\1=[redacted]", value)
    return value.replace(str(PROJECT), "[project]")


def run(label: str, command: list[str], timeout: int = 180) -> dict[str, Any]:
    cp = subprocess.run(command, cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    out = safe((cp.stdout or "") + "\n" + (cp.stderr or ""))
    return {"label": label, "returncode": cp.returncode, "output_sha256": sha_text(out), "tail": out.splitlines()[-40:]}


def health(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read(20_000)
            return {"ok": True, "status": getattr(resp, "status", None), "final_url": resp.geturl(), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    except Exception as exc:
        return {"ok": False, "error_type": type(exc).__name__, "error_sha256": sha_text(str(exc))}


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    before = {
        "listener": run("listener_before", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':8020\\b' || true"], 30),
        "health": health("http://127.0.0.1:8020/healthz"),
    }
    if (PROJECT / "scripts/dashboard/restart_dashboard.sh").exists():
        action = run("restart_dashboard", ["bash", "scripts/dashboard/restart_dashboard.sh"], 240)
    else:
        stop = run("stop_dashboard", ["bash", "scripts/dashboard/stop_dashboard.sh"], 180)
        start = run("start_dashboard", ["bash", "scripts/dashboard/start_dashboard.sh"], 180)
        action = {"label": "stop_start_dashboard", "returncode": 0 if stop["returncode"] == 0 and start["returncode"] == 0 else 1, "stop": stop, "start": start}
    ready = None
    for _ in range(25):
        ready = health("http://127.0.0.1:8020/healthz")
        if ready.get("ok"):
            break
        time.sleep(1)
    after = {
        "listener": run("listener_after", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':8020\\b' || true"], 30),
        "health": ready,
        "root_redirect_check": health("http://127.0.0.1:8020/"),
    }
    passed = bool(ready and ready.get("ok") and after["listener"]["returncode"] == 0)
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "service": "dashboard",
        "port": 8020,
        "reason": "Reload dashboard so the prediction demo panel and API route are active.",
        "before": before,
        "action": action,
        "after": after,
        "ingestion_or_promotion_triggered": False,
        "trusted_snapshot_promotion_human_decision_writes": 0,
        "passed": passed,
    }
    (REPORT / "dashboard_reload_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "DASHBOARD_RELOAD_COMPLETE" if passed else "DASHBOARD_RELOAD_FAILED", "passed": passed, "health": ready}, ensure_ascii=False, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

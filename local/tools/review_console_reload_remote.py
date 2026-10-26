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


def run(label: str, command: list[str], timeout: int = 120) -> dict[str, Any]:
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


def listener_pids(probe: dict[str, Any]) -> list[str]:
    joined = "\n".join(str(x) for x in probe.get("tail") or [])
    return sorted(set(re.findall(r"pid=(\d+)", joined)))


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    before = {
        "status_script": run("status_before", ["bash", "scripts/source_review/status_source_review.sh"], 120),
        "listener": run("listener_before", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':8021\\b' || true"], 30),
        "health": health("http://127.0.0.1:8021/healthz"),
    }
    stop = run("stop_source_review", ["bash", "scripts/source_review/stop_source_review.sh"], 180)
    start = run("start_source_review", ["bash", "scripts/source_review/start_source_review.sh"], 180)
    ready = None
    for _ in range(20):
        ready = health("http://127.0.0.1:8021/healthz")
        if ready.get("ok"):
            break
        time.sleep(1)
    after = {
        "status_script": run("status_after", ["bash", "scripts/source_review/status_source_review.sh"], 120),
        "listener": run("listener_after", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':8021\\b' || true"], 30),
        "health": ready,
        "review_case_redirect_check": health("http://127.0.0.1:8021/reviews/668e3dd5-cbdf-5e19-af8b-5360ef9e1af9"),
    }
    before_pids = listener_pids(before["listener"])
    after_pids = listener_pids(after["listener"])
    listener_ok = after["listener"]["returncode"] == 0 and bool(after_pids)
    pid_changed = bool(before_pids and after_pids and before_pids != after_pids)
    start_warning_only = start["returncode"] != 0 and listener_ok and ready and ready.get("ok")
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "service": "source_review",
        "port": 8021,
        "reason": "Reload Review Console so the patched raw-source summary UI code is active.",
        "before": before,
        "stop": stop,
        "start": start,
        "after": after,
        "listener_pids_before": before_pids,
        "listener_pids_after": after_pids,
        "listener_pid_changed": pid_changed,
        "start_returncode_warning_only": bool(start_warning_only),
        "ingestion_or_promotion_triggered": False,
        "trusted_snapshot_promotion_human_decision_writes": 0,
        "passed": bool(ready and ready.get("ok") and listener_ok and stop["returncode"] == 0),
    }
    (REPORT / "review_console_reload_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "REVIEW_CONSOLE_RELOAD_COMPLETE" if report["passed"] else "REVIEW_CONSOLE_RELOAD_FAILED", "passed": report["passed"], "health": ready}, ensure_ascii=False, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

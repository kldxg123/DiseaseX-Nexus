from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import migrate_epidemic_to_a100 as migration  # noqa: E402


PROJECT_SUFFIX = "/autodl-tmp/epidemic-openclaw"
LOCAL_DASHBOARD = "http://127.0.0.1:8120"
LOCAL_REVIEW = "http://127.0.0.1:8121"


def q(value: str) -> str:
    return migration.q(value)


def run(client, command: str, timeout: int = 300) -> tuple[int, str, str]:
    return migration.run(client, command, timeout=timeout)


def checked(client, command: str, timeout: int = 300) -> str:
    return migration.checked(client, command, timeout=timeout)


def remote_http_status(client, url: str) -> dict[str, object]:
    code = r"""
import json
import urllib.error
import urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
url = __import__("sys").argv[1]
try:
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(url, timeout=5)
        print(json.dumps({"ok": True, "status": resp.status, "location": resp.headers.get("location")}))
    except urllib.error.HTTPError as exc:
        print(json.dumps({"ok": True, "status": exc.code, "location": exc.headers.get("location")}))
except Exception as exc:
    print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}))
"""
    out = checked(client, f"python3 -c {q(code)} {q(url)}", timeout=30)
    return json.loads(out)


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def local_http_ready(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def stop_recorded_tunnel(pid_file: Path, python_path: str) -> None:
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except Exception:
        pid_file.unlink(missing_ok=True)
        return
    # Avoid command-line inspection; only terminate the previously recorded Python tunnel.
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                f"if($p -and $p.Path -eq {json.dumps(python_path)})"
                "{$p | Stop-Process -Force -ErrorAction SilentlyContinue}"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    pid_file.unlink(missing_ok=True)


def start_tunnel() -> dict[str, object]:
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    pid_file = runtime / "a100_project_tunnel.pid"
    known_hosts = runtime / "known_hosts_a100_epidemic"
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
    python = str(bundled if bundled.exists() else Path(sys.executable))
    stop_recorded_tunnel(pid_file, python)
    tunnel = ROOT / "tools" / "hold_a100_project_tunnel.py"
    proc = subprocess.Popen(
        [python, str(tunnel), "--pid-file", str(pid_file), "--known-hosts", str(known_hosts)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # hold_a100_project_tunnel writes its own pid after SSH/bind succeeds; keep our PID as fallback.
    time.sleep(0.5)
    if not pid_file.exists():
        pid_file.write_text(str(proc.pid), encoding="ascii")
    for _ in range(45):
        if proc.poll() is not None:
            break
        if local_http_ready(f"{LOCAL_DASHBOARD}/readyz") and local_http_ready(f"{LOCAL_REVIEW}/readyz"):
            return {"status": "PASS", "pid_file": str(pid_file), "process_pid": proc.pid}
        time.sleep(1)
    return {"status": "FAILED", "pid_file": str(pid_file), "process_pid": proc.pid, "exited": proc.poll() is not None}


def main() -> None:
    creds = migration.credentials(migration.TARGET_INFO)
    client, gate = migration.connect_with_gate("target", creds)
    try:
        home = checked(client, "printf '%s' \"$HOME\"", timeout=30)
        project = home.rstrip("/") + PROJECT_SUFFIX
        services = [
            ("postgres", "scripts/postgres/status.sh", "scripts/postgres/start.sh", 600),
            ("query_api", "scripts/query_api/status_query_api.sh", "scripts/query_api/start_query_api.sh", 300),
            ("dashboard", "scripts/dashboard/status_dashboard.sh", "scripts/dashboard/start_dashboard.sh", 300),
            ("review_console", "scripts/source_review/status_source_review.sh", "scripts/source_review/start_source_review.sh", 300),
        ]
        service_results: dict[str, object] = {}
        for name, status_rel, start_rel, timeout in services:
            status_cmd = q(project + "/" + status_rel)
            rc, _out, _err = run(client, status_cmd, timeout=90)
            started = False
            if rc != 0:
                start_cmd = (
                    "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                    + q(project + "/" + start_rel)
                )
                rc_start, _out_start, _err_start = run(client, start_cmd, timeout=timeout)
                started = True
                if rc_start != 0:
                    service_results[name] = {"status": "FAILED_TO_START", "started": True, "start_exit": rc_start}
                    continue
            rc2, _out2, _err2 = run(client, status_cmd, timeout=90)
            service_results[name] = {"status": "PASS" if rc2 == 0 else "UNHEALTHY", "started": started, "status_exit": rc2}
        remote_health = {
            "dashboard_readyz": remote_http_status(client, "http://127.0.0.1:8020/readyz"),
            "review_readyz": remote_http_status(client, "http://127.0.0.1:8021/readyz"),
            "fasttrack_unauth": remote_http_status(client, "http://127.0.0.1:8020/fasttrack-forecast"),
        }
        tunnel = start_tunnel()
        print(
            json.dumps(
                {
                    "gate_observed": bool(gate),
                    "services": service_results,
                    "remote_health": remote_health,
                    "tunnel": tunnel,
                    "urls": {
                        "dashboard_root": LOCAL_DASHBOARD,
                        "fasttrack": f"{LOCAL_DASHBOARD}/fasttrack-forecast",
                        "p1_12_trusted": f"{LOCAL_DASHBOARD}/p1-12/trusted",
                        "review_console": f"{LOCAL_REVIEW}/p1-12/reviews",
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()

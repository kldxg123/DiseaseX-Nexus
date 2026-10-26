from __future__ import annotations

import json
import re
from pathlib import Path


project = Path(".").resolve()
app = project / "src/epidemic_core/dashboard/app.py"
text = app.read_text(encoding="utf-8")
patterns = ["DASHBOARD_AUTH_FILE", "login", "_authorized", "SESSION", "cookie", "password"]
hits = []
for i, line in enumerate(text.splitlines(), start=1):
    if any(p.lower() in line.lower() for p in patterns):
        sanitized = re.sub(r'(["\'])([^"\']*(?:password|secret|token|cookie)[^"\']*)(["\'])', r'\1[redacted-key]\3', line, flags=re.I)
        hits.append({"line": i, "text": sanitized[:240]})
env_paths = [
    Path.home() / "epidemic-dashboard-runtime" / "secrets" / "dashboard.env",
    project.parent / "epidemic-dashboard-runtime" / "secrets" / "dashboard.env",
]
env_keys = []
env_path_found = None
for env_path in env_paths:
    if env_path.is_file():
        env_path_found = str(env_path)
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                env_keys.append(line.split("=", 1)[0])
        break
auth_files = []
for root in [Path.home() / "epidemic-dashboard-runtime", project / "storage"]:
    if root.exists():
        for p in root.rglob("*auth*"):
            if p.is_file():
                auth_files.append(str(p))
proc_auth = None
run_dir = project.parent / "epidemic-dashboard-runtime" / "run"
pid_path = run_dir / "dashboard.pid"
if pid_path.is_file():
    try:
        pid = pid_path.read_text(encoding="ascii").strip()
        env = (Path("/proc") / pid / "environ").read_bytes().split(b"\0")
        for item in env:
            if item.startswith(b"DASHBOARD_AUTH_FILE="):
                proc_auth = item.split(b"=", 1)[1].decode("utf-8", "replace")
    except Exception:
        proc_auth = None
auth_format = None
if proc_auth and Path(proc_auth).is_file():
    try:
        import json as _json
        data = _json.loads(Path(proc_auth).read_text(encoding="utf-8"))
        auth_format = {"keys": sorted(data.keys()), "username_present": "username" in data, "hash_present": any(k in data for k in ("hash", "password_hash", "digest"))}
    except Exception as exc:
        auth_format = {"parse_error": type(exc).__name__}
print(json.dumps({"app_hits": hits[:80], "dashboard_env_path_found": bool(env_path_found), "dashboard_env_keys": env_keys, "process_auth_file_present": bool(proc_auth), "auth_format": auth_format, "auth_file_candidates_count": len(auth_files), "auth_file_candidate_names": [Path(x).name for x in auth_files[:20]]}, ensure_ascii=False, indent=2))

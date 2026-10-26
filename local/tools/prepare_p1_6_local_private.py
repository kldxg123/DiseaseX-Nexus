from __future__ import annotations

import secrets
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0

ROOT = Path(r"F:\作业\aibola")
SECRET_DIR = ROOT / "secrets"
PRIVATE_DIR = ROOT / "private"
PASSWORD_FILE = SECRET_DIR / "dashboard_login.txt"
TUNNEL_FILE = PRIVATE_DIR / "open_p1_6_dashboard.ps1"
KNOWN_HOSTS = PRIVATE_DIR / "known_hosts_autodl_p1_6_current"


def restrict(path: Path) -> None:
    machine = os.environ.get("COMPUTERNAME", "")
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{Path.home().name}:F", f"{machine}\\CodexSandboxUsers:F"],
        check=True,
        capture_output=True,
    )


def main() -> None:
    SECRET_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    if not PASSWORD_FILE.exists():
        PASSWORD_FILE.write_text("analyst\n" + secrets.token_urlsafe(36) + "\n", encoding="utf-8")
    host, port, user, _password = p0.creds()
    script = f'''$ErrorActionPreference = "Stop"
$knownHosts = "{str(KNOWN_HOSTS).replace(chr(92), chr(92) * 2)}"
Write-Host "正在建立只读疫情仪表板 SSH 隧道；SSH 密码由 ssh 交互提示输入。"
& ssh -N -T `
  -L 127.0.0.1:8020:127.0.0.1:8020 `
  -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=30 `
  -o ServerAliveCountMax=3 `
  -o StrictHostKeyChecking=yes `
  -o UserKnownHostsFile="$knownHosts" `
  -p {port} {user}@{host}
'''
    TUNNEL_FILE.write_text(script, encoding="utf-8")
    for path in (PASSWORD_FILE, TUNNEL_FILE):
        if path.exists():
            restrict(path)
    print("P1_6_LOCAL_PRIVATE_READY")


if __name__ == "__main__":
    main()

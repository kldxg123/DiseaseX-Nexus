from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0


def acl(path: Path) -> str:
    return subprocess.check_output(["icacls", str(path)], text=True, errors="ignore")


def main() -> None:
    root=Path(r"F:\作业\aibola");password_file=root/"secrets/dashboard_login.txt";tunnel_file=root/"private/open_p1_6_dashboard.ps1";known=root/"private/known_hosts_autodl_p1_6_current"
    lines=password_file.read_text(encoding="utf-8").splitlines();assert len(lines)>=2 and lines[0]=="analyst" and len(lines[1])>=40
    tunnel=tunnel_file.read_text(encoding="utf-8");server_password=p0.creds()[3]
    assert lines[1] not in tunnel and server_password not in tunnel
    for marker in ("-N -T","127.0.0.1:8020:127.0.0.1:8020","ExitOnForwardFailure=yes","ServerAliveInterval=30","ServerAliveCountMax=3","StrictHostKeyChecking=yes","UserKnownHostsFile"):
        assert marker in tunnel
    for path in (password_file,tunnel_file):
        value=acl(path);assert "ADMIN:(F)" in value and "CodexSandboxUsers:(F)" in value and "Authenticated Users" not in value and "BUILTIN\\Users" not in value
    assert known.exists()
    print("P1_6_LOCAL_PRIVATE_VERIFY_PASS")


if __name__=="__main__":main()

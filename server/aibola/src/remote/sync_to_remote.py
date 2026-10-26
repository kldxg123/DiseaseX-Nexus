from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.remote.p0b_utils import mirror_report, remote_project_prelude, write_local_report
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


SYNC_PATHS = [
    "configs",
    "src",
    "scripts",
    "tests",
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "app_quantitative.py",
    "data/raw",
    "data/processed",
]


VERIFY_AND_SCAN = r"""
if ! command -v python3 >/dev/null 2>&1; then
  echo '{"status":"blocked","blocked_reason":"python3_not_found_after_sync"}'
  exit 2
fi
python3 -m compileall src
python3 - <<'PY'
import fnmatch
import json
from pathlib import Path

blocked = []
warnings = []
checked_terms = ["服务器信息", "password", "passwd", "HF_TOKEN", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY", ".pem", "id_rsa"]
for required in ["src/remote/detect_server.py", "data/raw", "data/processed"]:
    if not Path(required).exists():
        blocked.append({"type": "missing_required_sync_path", "path": required})

for path in Path(".").rglob("*"):
    if not path.is_file():
        continue
    if path.as_posix().startswith(("data/outputs/", "logs/")):
        continue
    if "__pycache__" in path.parts:
        continue
    rel = path.as_posix()
    name = path.name
    forbidden_name = (
        "服务器信息" in name
        or name in {".env"}
        or fnmatch.fnmatch(name, ".env.*")
        or fnmatch.fnmatch(name, "server*.txt")
        or fnmatch.fnmatch(name, "server*.json")
        or fnmatch.fnmatch(name, "server*.yaml")
        or fnmatch.fnmatch(name, "server*.yml")
        or fnmatch.fnmatch(name, "remote*.yaml")
        or fnmatch.fnmatch(name, "remote*.yml")
        or fnmatch.fnmatch(name, "*.pem")
        or fnmatch.fnmatch(name, "*.key")
        or fnmatch.fnmatch(name, "id_rsa*")
    )
    if forbidden_name:
        try:
            path.unlink()
            action = "removed"
        except OSError:
            action = "remove_failed"
        blocked.append({"type": "forbidden_secret_filename", "path": rel, "action": action})
        continue
    if path.stat().st_size > 2_000_000:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    is_scanner_source = rel == "src/remote/sync_to_remote.py"
    if not is_scanner_source and ("BEGIN OPENSSH PRIVATE KEY" in text or "BEGIN RSA PRIVATE KEY" in text):
        blocked.append({"type": "private_key_marker", "path": rel})
    if not is_scanner_source and ("HF_TOKEN=" in text or "HF_TOKEN:" in text):
        blocked.append({"type": "hf_token_assignment", "path": rel})
    for term in checked_terms:
        if term in text:
            warnings.append({"type": "term_mention", "term": term, "path": rel})

print(json.dumps({"status": "blocked" if blocked else "ok", "checked_terms": checked_terms, "blocked": blocked, "warnings": warnings[:80]}, ensure_ascii=False))
if blocked:
    raise SystemExit(3)
PY
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync code and MVP data to remote ~/aibola, then run compile and secret scan.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=900, dry_run=args.dry_run)
    sync_result = runner.sftp_put_selected(Path(args.root), cfg.remote_dir, SYNC_PATHS)
    verify_result = None
    if sync_result.returncode == 0:
        command = remote_project_prelude(cfg, create=True) + VERIFY_AND_SCAN
        verify_result = runner.run(command, "logs/remote/sync_to_remote_sanitized.log")
    status = "ok" if sync_result.returncode == 0 and verify_result and verify_result.returncode == 0 else "blocked"
    blocked_reason = ""
    if sync_result.returncode != 0:
        blocked_reason = f"remote_sftp_sync_failed_returncode_{sync_result.returncode}"
    elif verify_result and verify_result.returncode != 0:
        blocked_reason = f"remote_compile_or_secret_scan_failed_returncode_{verify_result.returncode}"
    body = (
        f"status: {status}\n"
        f"remote_dir: {cfg.remote_dir}\n"
        f"synced_paths: {json.dumps(SYNC_PATHS, ensure_ascii=False)}\n"
        f"blocked_reason: {blocked_reason or 'none'}\n\n"
        "## Sync Output\n\n"
        "```text\n"
        f"{(sync_result.stdout or sync_result.stderr).strip()}\n"
        "```\n\n"
        "## Compile And Secret Scan\n\n"
        "```text\n"
        f"{((verify_result.stdout or verify_result.stderr).strip() if verify_result else 'not_run')}\n"
        "```\n"
    )
    local_report = write_local_report("remote_sync_report.md", "Remote Sync Report", body)
    mirror_code = mirror_report(runner, local_report, "remote_sync_report.md") if sync_result.returncode == 0 else 0
    print(sync_result.stdout or sync_result.stderr)
    if verify_result:
        print(verify_result.stdout or verify_result.stderr)
    if mirror_code != 0:
        return mirror_code
    return sync_result.returncode if sync_result.returncode != 0 else (verify_result.returncode if verify_result else 1)


if __name__ == "__main__":
    raise SystemExit(main())

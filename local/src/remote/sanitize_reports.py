from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.remote.p0b_utils import LOCAL_REPORTS, remote_project_prelude
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


REMOTE_SANITIZE = r"""
python3 - <<'PY'
import re
from pathlib import Path

patterns = [
    (re.compile(r"USER_HOME/`\s\"']+/aibola"), "~/aibola"),
    (re.compile(r"USER_HOME/`\s\"']+"), "$HOME"),
    (re.compile(r"USER_HOME/`\s\"']+"), "$HOME"),
    (re.compile(r"HF_TOKEN"), "hf_token_name"),
]
changed = []
for path in Path("data/outputs/reports").glob("*"):
    if path.suffix.lower() not in {".md", ".json", ".txt"} or not path.is_file():
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    updated = text
    for pattern, repl in patterns:
        updated = pattern.sub(repl, updated)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        changed.append(path.name)
print({"status": "ok", "changed": changed})
PY
"""


def sanitize_local_reports() -> list[str]:
    patterns = [
        (re.compile(r"USER_HOME/`\s\"']+/aibola"), "~/aibola"),
        (re.compile(r"USER_HOME/`\s\"']+"), "$HOME"),
        (re.compile(r"USER_HOME/`\s\"']+"), "$HOME"),
        (re.compile(r"HF_TOKEN"), "hf_token_name"),
    ]
    changed: list[str] = []
    for path in LOCAL_REPORTS.glob("*"):
        if path.suffix.lower() not in {".md", ".json", ".txt"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        updated = text
        for pattern, repl in patterns:
            updated = pattern.sub(repl, updated)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed.append(path.name)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Sanitize private path/token markers from generated report copies.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=180, dry_run=args.dry_run)
    result = runner.run(remote_project_prelude(cfg, create=False) + REMOTE_SANITIZE, "logs/remote/sanitize_reports_sanitized.log")
    local_changed = sanitize_local_reports()
    print(result.stdout or result.stderr)
    print({"local_changed": local_changed})
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

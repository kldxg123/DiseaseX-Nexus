from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass

from src.remote.p0b_utils import remote_project_prelude
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


ACTIVATE_ENV = r"""
if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
  . "$HOME/miniconda3/etc/profile.d/conda.sh"
  conda activate "__CONDA_ENV__" || true
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
  . "$HOME/anaconda3/etc/profile.d/conda.sh"
  conda activate "__CONDA_ENV__" || true
fi
mkdir -p data/outputs/reports data/outputs/figures data/processed logs models
"""


UPDATE_STATUS = r"""
P0C1_STAGE="__STAGE__" P0C1_COMMAND="__COMMAND__" P0C1_CODE="$CODE" python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

reports = Path("data/outputs/reports")
reports.mkdir(parents=True, exist_ok=True)
path = reports / "p0c1_stage_status.json"
if path.exists():
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
else:
    payload = {}
stages = payload.setdefault("stages", {})
code = int(os.environ["P0C1_CODE"])
stages[os.environ["P0C1_STAGE"]] = {
    "command": os.environ["P0C1_COMMAND"],
    "returncode": code,
    "status": "ok" if code == 0 else "blocked",
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
payload["status"] = "blocked" if any(item.get("returncode", 0) != 0 for item in stages.values()) else "ok"
payload["blocked_reason"] = "none" if payload["status"] == "ok" else ",".join(
    f"{name}_returncode_{item.get('returncode')}" for name, item in stages.items() if item.get("returncode", 0) != 0
)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY
"""


@dataclass(frozen=True)
class P0c1Stage:
    name: str
    command: str
    timeout: int = 1800


def run_stage(stage: P0c1Stage, root: str = ".", dry_run: bool = False) -> int:
    cfg = ServerConfig.from_project_root(root)
    runner = SSHRunner(cfg, timeout=stage.timeout, dry_run=dry_run)
    update_status = (
        UPDATE_STATUS.replace('P0C1_STAGE="__STAGE__"', f"P0C1_STAGE={shlex.quote(stage.name)}")
        .replace('P0C1_COMMAND="__COMMAND__"', f"P0C1_COMMAND={shlex.quote(stage.command)}")
    )
    command = (
        remote_project_prelude(cfg, create=True)
        + ACTIVATE_ENV.replace("__CONDA_ENV__", cfg.conda_env)
        + "set +e\n"
        + f"bash -lc {shlex.quote(stage.command)}\n"
        + "CODE=$?\n"
        + update_status
        + "exit $CODE\n"
    )
    result = runner.run(command, f"logs/remote/{stage.name}_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


def parse_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()

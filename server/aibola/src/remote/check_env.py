from __future__ import annotations

import argparse

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Check remote project and Python environment.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    command = f"""
set -eu
cd {cfg.remote_dir}
echo "check:project_files"
test -f README.md && echo "README.md ok"
test -f requirements.txt && echo "requirements.txt ok"
test -d src && echo "src ok"
test -f src/__init__.py && echo "src/__init__.py ok"
test -f src/api/server.py && echo "src/api/server.py ok"
test -f src/models/qwen25_lora_dataset.py && echo "src/models/qwen25_lora_dataset.py ok"
test -f src/remote/server_config.py && echo "src/remote/server_config.py ok"
CONDA_SH=""
for candidate in "$HOME/miniconda3/etc/profile.d/conda.sh" "$HOME/anaconda3/etc/profile.d/conda.sh" "$HOME/conda3/etc/profile.d/conda.sh" "/opt/conda/etc/profile.d/conda.sh"; do
  if [ -f "$candidate" ]; then CONDA_SH="$candidate"; break; fi
done
if [ -n "$CONDA_SH" ]; then
  . "$CONDA_SH"
  echo "check:python"
  conda run -n {cfg.conda_env} python --version
  echo "check:imports"
  conda run -n {cfg.conda_env} python -c "import importlib.util as u; print({{'pytest': bool(u.find_spec('pytest')), 'pandas': bool(u.find_spec('pandas')), 'fastapi': bool(u.find_spec('fastapi')), 'huggingface_hub': bool(u.find_spec('huggingface_hub')), 'torch': bool(u.find_spec('torch')), 'bitsandbytes': bool(u.find_spec('bitsandbytes'))}})"
  echo "check:compile"
  conda run -n {cfg.conda_env} python -m compileall -q src
  echo "check:sys_path_import"
  conda run -n {cfg.conda_env} python -c "import sys; print(sys.path[:3]); import src; print(src.__file__)"
else
  echo "conda_not_found" >&2
  exit 2
fi
"""
    result = SSHRunner(cfg, timeout=300, dry_run=args.dry_run).run(command, "logs/remote/check_env_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

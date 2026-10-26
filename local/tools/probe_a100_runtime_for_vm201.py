from __future__ import annotations

from pathlib import Path

from src.remote.server_config import _parse_positional_secret_file
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    cfg_path = Path("服务器信息a100.txt")
    cfg = _parse_positional_secret_file(
        cfg_path,
        cfg_path.read_text(encoding="utf-8", errors="ignore"),
    )
    if cfg is None:
        print("A100_CONFIG_PARSE_FAILED")
        return 2
    command = r"""
set -eu
echo A100_RUNTIME_PROBE
for p in \
  USER_HOME/autodl-tmp/conda/envs/postgresql18/bin \
  USER_HOME/autodl-tmp/conda/envs/postgresql18/bin \
  USER_HOME/miniconda3/envs/postgresql18/bin \
  /opt/conda/envs/postgresql18/bin
do
  if [ -x "$p/psql" ]; then
    echo "PG_BIN_FOUND=$p"
    "$p/psql" --version
    "$p/pg_restore" --version
    "$p/pg_ctl" --version
  fi
done
echo PROJECT_PROBE
for d in \
  USER_HOME/autodl-tmp/epidemic-openclaw \
  USER_HOME/autodl-tmp/epidemic-openclaw
do
  if [ -d "$d" ]; then
    echo "PROJECT_FOUND=$d"
  fi
done
echo DOCKER_PROBE
if command -v docker >/dev/null 2>&1; then
  docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^postgres:' || true
else
  echo DOCKER_NOT_FOUND
fi
"""
    result = SSHRunner(cfg, timeout=60).run(command)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

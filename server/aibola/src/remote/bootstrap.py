from __future__ import annotations

import argparse

from src.remote.p0b_utils import pull_report, remote_project_prelude, write_local_report
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


BOOTSTRAP_SCRIPT = r"""
CONDA_ENV="__CONDA_ENV__"
mkdir -p data/outputs/reports logs
CONDA_SH=""
for candidate in "$HOME/miniconda3/etc/profile.d/conda.sh" "$HOME/anaconda3/etc/profile.d/conda.sh" "$HOME/conda3/etc/profile.d/conda.sh" "/opt/conda/etc/profile.d/conda.sh"; do
  if [ -f "$candidate" ]; then
    CONDA_SH="$candidate"
    break
  fi
done

CORE_DEPS="pandas numpy scikit-learn pyarrow pyyaml requests tenacity tqdm rich pytest paramiko"
SERVICE_DEPS="fastapi uvicorn pydantic matplotlib gradio"
TRAINING_DEPS="torch transformers datasets accelerate peft trl bitsandbytes lightgbm xgboost"
PY_CMD=""
PIP_CMD=""
ENV_KIND=""
BOOTSTRAP_WARNINGS=""
TRAINING_WARNINGS=""

if [ -n "$CONDA_SH" ]; then
  . "$CONDA_SH"
  conda create -n "$CONDA_ENV" python=3.10 -y || true
  PY_CMD="conda run -n $CONDA_ENV python"
  PIP_CMD="conda run -n $CONDA_ENV python -m pip"
  ENV_KIND="conda"
elif command -v python3 >/dev/null 2>&1; then
  python3 -m venv .venv
  PY_CMD=".venv/bin/python"
  PIP_CMD=".venv/bin/python -m pip"
  ENV_KIND="venv"
else
  cat > data/outputs/reports/remote_bootstrap_report.md <<'EOF'
# Remote Bootstrap Report

status: blocked
blocked_reason: python3_or_conda_not_found
EOF
  exit 2
fi

$PIP_CMD install --upgrade pip
CORE_STATUS="ok"
SERVICE_STATUS="ok"
TRAINING_STATUS="ok"
if ! $PIP_CMD install $CORE_DEPS; then
  CORE_STATUS="install_failed"
fi
if ! $PIP_CMD install $SERVICE_DEPS; then
  SERVICE_STATUS="warning_install_failed"
  BOOTSTRAP_WARNINGS="$BOOTSTRAP_WARNINGS service_dependency_install_failed"
fi
if ! $PIP_CMD install $TRAINING_DEPS; then
  TRAINING_STATUS="warning_install_failed"
  TRAINING_WARNINGS="$TRAINING_WARNINGS training_dependency_install_failed_p0c_may_be_blocked"
fi

cat > data/outputs/reports/_bootstrap_check.py <<'PY'
import importlib.util
import json
import os
from pathlib import Path

core = {
    "pandas": "pandas",
    "numpy": "numpy",
    "scikit-learn": "sklearn",
    "pyarrow": "pyarrow",
    "pyyaml": "yaml",
    "requests": "requests",
    "tenacity": "tenacity",
    "tqdm": "tqdm",
    "rich": "rich",
    "pytest": "pytest",
    "paramiko": "paramiko",
}
service = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "matplotlib": "matplotlib",
    "gradio": "gradio",
}
training = {
    "torch": "torch",
    "transformers": "transformers",
    "datasets": "datasets",
    "accelerate": "accelerate",
    "peft": "peft",
    "trl": "trl",
    "bitsandbytes": "bitsandbytes",
    "lightgbm": "lightgbm",
    "xgboost": "xgboost",
}

def available(group):
    return {name: bool(importlib.util.find_spec(module)) for name, module in group.items()}

core_available = available(core)
service_available = available(service)
training_available = available(training)
missing_core = [name for name, ok in core_available.items() if not ok]
status = "blocked" if missing_core else "ok"
blocked_reason = "missing_core_dependencies:" + ",".join(missing_core) if missing_core else "none"
report = {
    "status": status,
    "blocked_reason": blocked_reason,
    "environment": "__CONDA_ENV__",
    "environment_kind": os.environ.get("ENV_KIND_VALUE", ""),
    "core_install_status": os.environ.get("CORE_STATUS_VALUE", ""),
    "service_install_status": os.environ.get("SERVICE_STATUS_VALUE", ""),
    "training_install_status": os.environ.get("TRAINING_STATUS_VALUE", ""),
    "warnings": os.environ.get("BOOTSTRAP_WARNINGS_VALUE", "").strip().split(),
    "p0c_blockers": os.environ.get("TRAINING_WARNINGS_VALUE", "").strip().split(),
    "core_dependencies": core_available,
    "service_dependencies": service_available,
    "training_dependencies": training_available,
}
body = [
    "# Remote Bootstrap Report",
    "",
    f"status: {status}",
    f"blocked_reason: {blocked_reason}",
    f"environment: {report['environment']}",
    f"environment_kind: {report['environment_kind']}",
    f"core_install_status: {report['core_install_status']}",
    f"service_install_status: {report['service_install_status']}",
    f"training_install_status: {report['training_install_status']}",
    "",
    "```json",
    json.dumps(report, ensure_ascii=False, indent=2),
    "```",
]
Path("data/outputs/reports/remote_bootstrap_report.md").write_text("\n".join(body) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(4 if missing_core else 0)
PY
ENV_KIND_VALUE="$ENV_KIND" \
CORE_STATUS_VALUE="$CORE_STATUS" \
SERVICE_STATUS_VALUE="$SERVICE_STATUS" \
TRAINING_STATUS_VALUE="$TRAINING_STATUS" \
BOOTSTRAP_WARNINGS_VALUE="$BOOTSTRAP_WARNINGS" \
TRAINING_WARNINGS_VALUE="$TRAINING_WARNINGS" \
$PY_CMD data/outputs/reports/_bootstrap_check.py
rm -f data/outputs/reports/_bootstrap_check.py
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap remote Python environment.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=1800, dry_run=args.dry_run)
    script = (
        remote_project_prelude(cfg, create=True)
        + BOOTSTRAP_SCRIPT.replace("__CONDA_ENV__", cfg.conda_env)
    )
    result = runner.run(script, "logs/remote/bootstrap_sanitized.log")
    pull_code = pull_report(runner, "remote_bootstrap_report.md")
    if pull_code != 0:
        write_local_report(
            "remote_bootstrap_report.md",
            "Remote Bootstrap Report",
            f"status: unknown\nblocked_reason: report_pull_failed\n\n```text\n{(result.stdout or result.stderr).strip()}\n```",
        )
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

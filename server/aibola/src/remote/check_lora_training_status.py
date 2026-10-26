from __future__ import annotations

import argparse
import json
import shlex

from src.remote.p0b_utils import remote_project_prelude
from src.remote.p0c1_runner import ACTIVATE_ENV
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


DEFAULT_SESSION = "aibola_qwen25_lora_20260623_011633"
DEFAULT_LOG_PATH = "~/aibola/logs/qwen25_lora_train_qwen25_lora_20260623_011633.log"


REMOTE_CHECK = r'''
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.reports.p0c2_status import build_status, write_status_report
from src.utils.io import ensure_dir, write_json

SESSION = os.environ["AIBOLA_LORA_SESSION"]
LOG_PATH = Path(os.environ["AIBOLA_LORA_LOG"].replace("~", str(Path.home()), 1))
REPORTS = ensure_dir("data/outputs/reports")
REQUIRED = ["adapter_config.json", "adapter_model.safetensors", "run_manifest.json", "train_metrics.json"]
COMPLETION_SIGNALS = [
    "Training completed",
    "save_pretrained completed",
    "adapter saved",
    "train_metrics.json written",
    "\"training_status\": \"completed\"",
    "'training_status': 'completed'",
]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sanitize(text: str) -> str:
    text = text.replace(str(Path.home()), "$HOME")
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    if token:
        text = text.replace(token, "<redacted>")
    patterns = [
        (r"(?i)(HF_TOKEN|HUGGINGFACE_HUB_TOKEN|token|password|passwd)\s*[:=]\s*[^ \n\r]+", r"\1=<redacted>"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "<redacted-private-key>"),
    ]
    for pattern, repl in patterns:
        text = re.sub(pattern, repl, text, flags=re.DOTALL)
    return text


def tail_text(path: Path, limit: int = 80) -> str:
    if not path.exists():
        return ""
    return sanitize("\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]))


def candidate_dir_from_report(train_report: dict) -> Path:
    value = train_report.get("candidate_output_dir") or train_report.get("candidate_adapter_path")
    if value and value != "not_registered_until_training_completes":
        return Path(value)
    run_id = train_report.get("run_id")
    if not run_id and SESSION.startswith("aibola_qwen25_lora_"):
        run_id = "qwen25_lora_" + SESSION.replace("aibola_qwen25_lora_", "")
    return Path("lora_weights/ebola_qwen25_step/candidates") / str(run_id or "unknown")


def write_train_report(payload: dict) -> None:
    write_json(REPORTS / "qwen_lora_train_report.json", payload)
    lines = [
        "# Qwen LoRA Train Report",
        "",
        f"status: {payload.get('status')}",
        f"blocked_reason: {payload.get('blocked_reason', 'none')}",
        f"training_status: {payload.get('training_status')}",
        f"tmux_session: {payload.get('tmux_session') or 'none'}",
        f"log_path: {payload.get('log_path') or 'none'}",
        f"gpu_used: {payload.get('gpu_used', 'not_available')}",
        f"candidate_adapter_path: {payload.get('candidate_adapter_path', 'not_registered_until_training_completes')}",
        f"eval_status: {payload.get('eval_status', 'not_started')}",
        f"registry_status: {payload.get('registry_status', 'not_candidate_yet')}",
        "promote: no",
    ]
    if payload.get("sanitized_tail_summary"):
        lines.extend(["", "## Sanitized Tail Summary", "```text", payload["sanitized_tail_summary"], "```"])
    (REPORTS / "qwen_lora_train_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_p0c2_status() -> None:
    write_status_report(build_status())


def main() -> int:
    existing = load_json(REPORTS / "qwen_lora_train_report.json")
    alive = subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True, text=True, check=False).returncode == 0
    tail = tail_text(LOG_PATH)
    now = datetime.now(timezone.utc).isoformat()
    base = {
        **existing,
        "checked_at": now,
        "tmux_session": SESSION,
        "log_path": LOG_PATH.as_posix().replace(str(Path.home()), "~"),
        "promote": "no",
    }
    if alive:
        payload = {
            **base,
            "status": "running",
            "blocked_reason": "none",
            "training_status": "running",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "eval_status": "not_started",
            "registry_status": "not_candidate_yet",
            "sanitized_tail_summary": tail,
        }
        write_train_report(payload)
        update_p0c2_status()
        print(json.dumps({"status": "running", "training_status": "running", "tmux_session_exists": True, "blocked_reason": "none"}, ensure_ascii=False))
        return 0

    adapter_dir = candidate_dir_from_report(existing)
    missing = [name for name in REQUIRED if not (adapter_dir / name).exists()]
    signal_found = any(signal in tail for signal in COMPLETION_SIGNALS)
    if missing or not signal_found:
        reasons = []
        if missing:
            reasons.append("missing_candidate_artifacts:" + ",".join(missing))
        if not signal_found:
            reasons.append("completion_signal_not_found")
        payload = {
            **base,
            "status": "blocked",
            "blocked_reason": ";".join(reasons),
            "training_status": "incomplete",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "candidate_output_dir": adapter_dir.as_posix(),
            "eval_status": "not_started",
            "registry_status": "not_candidate_yet",
            "required_files_present": False,
            "missing_required_files": missing,
            "completion_signal_found": signal_found,
            "sanitized_tail_summary": tail,
        }
        write_train_report(payload)
        update_p0c2_status()
        print(json.dumps({"status": "blocked", "training_status": "incomplete", "tmux_session_exists": False, "blocked_reason": payload["blocked_reason"]}, ensure_ascii=False))
        return 2

    payload = {
        **base,
        "status": "ok",
        "blocked_reason": "none",
        "training_status": "completed",
        "candidate_adapter_path": adapter_dir.as_posix(),
        "candidate_output_dir": adapter_dir.as_posix(),
        "eval_status": "not_started",
        "registry_status": "not_candidate_yet",
        "required_files_present": True,
        "missing_required_files": [],
        "completion_signal_found": True,
        "sanitized_tail_summary": tail,
    }
    write_train_report(payload)
    update_p0c2_status()
    print(json.dumps({"status": "completed", "training_status": "completed", "tmux_session_exists": False, "candidate_adapter_path": adapter_dir.as_posix(), "blocked_reason": "none"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Check current QLoRA tmux training status and update P0c-2 reports.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tmux-session", default=DEFAULT_SESSION)
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH)
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=180, dry_run=args.dry_run)
    command = (
        remote_project_prelude(cfg, create=False)
        + ACTIVATE_ENV.replace("__CONDA_ENV__", cfg.conda_env)
        + f"AIBOLA_LORA_SESSION={shlex.quote(args.tmux_session)} "
        + f"AIBOLA_LORA_LOG={shlex.quote(args.log_path)} "
        + "python - <<'PY'\n"
        + REMOTE_CHECK
        + "\nPY\n"
    )
    result = runner.run(command, "logs/remote/check_lora_training_status_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

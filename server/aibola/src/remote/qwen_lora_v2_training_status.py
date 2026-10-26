from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.reports.p0c4b_train_status import build_status, write_status
from src.utils.io import ensure_dir, write_json


REQUIRED = ["adapter_config.json", "adapter_model.safetensors", "run_manifest.json", "train_metrics.json"]
COMPLETION_SIGNALS = [
    "training_status\": \"completed",
    "'training_status': 'completed",
    "train_metrics.json",
    "status\": \"ok",
]


def load_json(path: Path) -> dict[str, Any]:
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


def tail_text(path: Path | None, limit: int = 80) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    return sanitize("\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]))


def write_train_report(payload: dict[str, Any]) -> None:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "qwen_lora_v2_train_report.json", payload)
    lines = [
        "# Qwen LoRA v2 Train Report",
        "",
        f"status: {payload.get('status')}",
        f"blocked_reason: {payload.get('blocked_reason', 'none')}",
        f"training_status: {payload.get('training_status')}",
        f"tmux_session: {payload.get('tmux_session') or 'none'}",
        f"log_path: {payload.get('log_path') or 'none'}",
        f"gpu_used: {payload.get('gpu_used', 'not_available')}",
        f"candidate_adapter_path: {payload.get('candidate_adapter_path', 'not_registered_until_training_completes')}",
        f"eval_status: {payload.get('eval_status', 'not_started')}",
        f"registry_status: {payload.get('registry_status', 'planned_or_running_not_candidate')}",
        "promote: no",
        "promote_allowed: false",
        "allow_auto_promote: false",
    ]
    if payload.get("sanitized_tail_summary"):
        lines.extend(["", "## Sanitized Tail Summary", "```text", payload["sanitized_tail_summary"], "```"])
    (reports / "qwen_lora_v2_train_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def eval_process_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "python -m src.remote.qwen_lora_v2_eval_runner"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Qwen LoRA v2 training status.")
    parser.add_argument("--tmux-session", default=None)
    parser.add_argument("--log-path", default=None)
    args = parser.parse_args()
    report_path = Path("data/outputs/reports/qwen_lora_v2_train_report.json")
    existing = load_json(report_path)
    session = args.tmux_session or existing.get("tmux_session") or "aibola_qwen25_lora_v2"
    log_value = args.log_path or existing.get("log_path") or ""
    log_path = Path(str(log_value).replace("~", str(Path.home()), 1)) if log_value else None
    alive = subprocess.run(["tmux", "has-session", "-t", str(session)], capture_output=True, text=True, check=False).returncode == 0
    tail = tail_text(log_path)
    base = {
        **existing,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tmux_session": session,
        "log_path": str(log_value),
        "promote": "no",
        "promote_allowed": False,
        "allow_auto_promote": False,
    }
    if alive:
        payload = {
            **base,
            "status": "running",
            "blocked_reason": "none",
            "training_status": "running",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "eval_status": "not_started",
            "registry_status": "planned_or_running_not_candidate",
            "sanitized_tail_summary": tail,
        }
        write_train_report(payload)
        write_status(build_status())
        print(json.dumps({"status": "running", "training_status": "running", "tmux_session_exists": True, "blocked_reason": "none"}, ensure_ascii=False))
        return 0

    adapter_value = existing.get("candidate_adapter_path")
    adapter_dir = Path(adapter_value) if adapter_value and adapter_value != "not_registered_until_training_completes" else None
    missing = [name for name in REQUIRED if not adapter_dir or not (adapter_dir / name).exists()]
    signal_found = existing.get("training_status") == "completed" or any(signal in tail for signal in COMPLETION_SIGNALS)
    if not missing and signal_found and eval_process_running():
        payload = {
            **base,
            "status": "running",
            "blocked_reason": "none",
            "training_status": "completed",
            "candidate_adapter_path": adapter_dir.as_posix() if adapter_dir else existing.get("candidate_adapter_path"),
            "eval_status": "running",
            "registry_status": "not_candidate_until_eval_and_compare_complete",
            "required_files_present": True,
            "missing_required_files": [],
            "completion_signal_found": True,
            "sanitized_tail_summary": tail,
        }
        write_train_report(payload)
        write_status(build_status())
        print(
            json.dumps(
                {
                    "status": "running",
                    "training_status": "completed",
                    "eval_status": "running",
                    "tmux_session_exists": False,
                    "candidate_adapter_path": payload["candidate_adapter_path"],
                    "blocked_reason": "none",
                },
                ensure_ascii=False,
            )
        )
        return 0
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
            "eval_status": "not_started",
            "registry_status": "planned",
            "required_files_present": False,
            "missing_required_files": missing,
            "completion_signal_found": signal_found,
            "sanitized_tail_summary": tail,
        }
        write_train_report(payload)
        write_status(build_status())
        print(json.dumps({"status": "blocked", "training_status": "incomplete", "tmux_session_exists": False, "blocked_reason": payload["blocked_reason"]}, ensure_ascii=False))
        return 2

    payload = {
        **base,
        "status": "ok",
        "blocked_reason": "none",
        "training_status": "completed",
        "candidate_adapter_path": adapter_dir.as_posix() if adapter_dir else existing.get("candidate_adapter_path"),
        "eval_status": existing.get("eval_status", "not_started"),
        "registry_status": "not_candidate_until_eval_and_compare_complete",
        "required_files_present": True,
        "missing_required_files": [],
        "completion_signal_found": True,
        "sanitized_tail_summary": tail,
    }
    write_train_report(payload)
    write_status(build_status())
    print(json.dumps({"status": "completed", "training_status": "completed", "tmux_session_exists": False, "candidate_adapter_path": payload["candidate_adapter_path"], "blocked_reason": "none"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

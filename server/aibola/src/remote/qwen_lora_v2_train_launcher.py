from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.remote.gpu_preflight import choose_gpu
from src.remote.qwen_lora_v2_gpu_preflight import write_v2_preflight_report
from src.reports.p0c4b_train_status import build_status, write_status
from src.utils.io import ensure_dir, write_json


BASE_SESSION = "aibola_qwen25_lora_v2"
REQUIRED = ["adapter_config.json", "adapter_model.safetensors", "run_manifest.json", "train_metrics.json"]


def run_id() -> str:
    return "qwen25_lora_v2_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def tmux_has_session(name: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", name], capture_output=True, text=True, check=False).returncode == 0


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


def sanitized_tail(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return ""
    return sanitize("\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:]))


def write_train_report(payload: dict[str, Any]) -> None:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "qwen_lora_v2_train_report.json", payload)
    lines = [
        "# Qwen LoRA v2 Train Report",
        "",
        f"status: {payload.get('status')}",
        f"blocked_reason: {payload.get('blocked_reason', 'none')}",
        f"training_status: {payload.get('training_status')}",
        f"run_id: {payload.get('run_id', 'none')}",
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


def write_status_report() -> None:
    write_status(build_status())


def gate_ok(path: str) -> bool:
    payload = load_json(Path(path))
    return payload.get("status") == "ok" or payload.get("training_status") == "dry_run_ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch full Qwen LoRA v2 training in tmux.")
    parser.add_argument("--conda-env", default="aibola")
    parser.add_argument("--verify-seconds", type=int, default=90)
    args = parser.parse_args()
    required_gates = {
        "dataset_validation": "data/outputs/reports/qwen_lora_v2_dataset_validation_report.json",
        "dryrun": "data/outputs/reports/qwen_lora_v2_dryrun_report.json",
    }
    failed = [name for name, path in required_gates.items() if not gate_ok(path)]
    if failed:
        payload = {
            "status": "blocked",
            "blocked_reason": "required_gates_not_ok:" + ",".join(failed),
            "training_status": "blocked",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "eval_status": "not_started",
            "registry_status": "planned",
            "promote": "no",
            "promote_allowed": False,
        }
        write_train_report(payload)
        write_status_report()
        print(json.dumps({"status": "blocked", "training_status": "blocked", "blocked_reason": payload["blocked_reason"]}, ensure_ascii=False))
        return 2

    preflight = choose_gpu("v2_train")
    write_v2_preflight_report(preflight, "data/outputs/reports/qwen_lora_v2_gpu.env")
    if preflight["status"] != "ok":
        payload = {
            "status": "blocked",
            "blocked_reason": preflight["blocked_reason"],
            "training_status": "blocked",
            "gpu_used": "none",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "eval_status": "not_started",
            "registry_status": "planned",
            "promote": "no",
            "promote_allowed": False,
            "gpu_preflight": preflight,
        }
        write_train_report(payload)
        write_status_report()
        print(json.dumps({"status": "blocked", "training_status": "blocked", "blocked_reason": payload["blocked_reason"]}, ensure_ascii=False))
        return 2

    rid = run_id()
    session = BASE_SESSION if not tmux_has_session(BASE_SESSION) else f"{BASE_SESSION}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    log_rel = f"logs/qwen25_lora_v2_train_{rid}.log"
    log_path = Path(log_rel)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    conda = (
        f"if [ -f $HOME/miniconda3/etc/profile.d/conda.sh ]; then . $HOME/miniconda3/etc/profile.d/conda.sh; conda activate {shlex.quote(args.conda_env)} || true; "
        f"elif [ -f $HOME/anaconda3/etc/profile.d/conda.sh ]; then . $HOME/anaconda3/etc/profile.d/conda.sh; conda activate {shlex.quote(args.conda_env)} || true; fi"
    )
    train_cmd = (
        "cd $HOME/aibola && "
        + conda
        + " ; "
        + f"TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false "
        + f"python -m src.remote.qwen_lora_v2_train_worker --gpu {preflight['selected_gpu']} --run-id-base {shlex.quote(rid)} "
        + f"2>&1 | tee {shlex.quote(log_rel)}"
    )
    launch = subprocess.run(["tmux", "new", "-d", "-s", session, train_cmd], capture_output=True, text=True, check=False)
    if launch.returncode != 0:
        payload = {
            "status": "failed",
            "blocked_reason": "tmux_launch_failed",
            "training_status": "failed",
            "gpu_used": preflight["selected_gpu"],
            "tmux_session": session,
            "log_path": "~/" + f"aibola/{log_rel}",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "eval_status": "not_started",
            "registry_status": "planned",
            "promote": "no",
            "promote_allowed": False,
            "tmux_error": sanitize(launch.stderr[:500]),
        }
        write_train_report(payload)
        write_status_report()
        print(json.dumps({"status": "failed", "training_status": "failed", "blocked_reason": "tmux_launch_failed"}, ensure_ascii=False))
        return 2

    time.sleep(max(60, min(args.verify_seconds, 120)))
    if tmux_has_session(session):
        payload = {
            "status": "running",
            "blocked_reason": "none",
            "training_status": "running",
            "run_id": rid,
            "gpu_used": preflight["selected_gpu"],
            "tmux_session": session,
            "log_path": "~/" + f"aibola/{log_rel}",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "candidate_output_dir": f"lora_weights/ebola_qwen25_step/candidates/{rid}",
            "eval_status": "not_started",
            "registry_status": "planned_or_running_not_candidate",
            "promote": "no",
            "promote_allowed": False,
            "allow_auto_promote": False,
            "gpu_preflight": preflight,
        }
        write_train_report(payload)
        write_status_report()
        print(json.dumps({"status": "running", "training_status": "running", "tmux_session": session, "gpu_used": preflight["selected_gpu"]}, ensure_ascii=False))
        return 0

    report = load_json(Path("data/outputs/reports/qwen_lora_v2_train_report.json"))
    if report.get("training_status") == "completed":
        report.update({"tmux_session": session, "log_path": "~/" + f"aibola/{log_rel}", "gpu_preflight": preflight})
        write_train_report(report)
        write_status_report()
        print(json.dumps({"status": "completed", "training_status": "completed", "tmux_session": session}, ensure_ascii=False))
        return 0

    payload = {
        **report,
        "status": "failed",
        "blocked_reason": report.get("blocked_reason", "tmux_session_exited_during_launch_verification"),
        "training_status": report.get("training_status", "failed"),
        "run_id": rid,
        "gpu_used": preflight["selected_gpu"],
        "tmux_session": session,
        "log_path": "~/" + f"aibola/{log_rel}",
        "candidate_adapter_path": "not_registered_until_training_completes",
        "eval_status": "not_started",
        "registry_status": "planned",
        "promote": "no",
        "promote_allowed": False,
        "sanitized_tail_summary": sanitized_tail(log_path),
        "gpu_preflight": preflight,
    }
    write_train_report(payload)
    write_status_report()
    print(json.dumps({"status": "failed", "training_status": payload["training_status"], "blocked_reason": payload["blocked_reason"], "tmux_session": session}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

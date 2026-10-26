from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.remote.gpu_preflight import choose_gpu, write_preflight_report
from src.reports.p0c2_status import build_status, write_status_report
from src.utils.io import ensure_dir, write_json


BASE_SESSION = "aibola_qwen25_lora"
BASE_LOG = "~/aibola/logs/qwen25_lora_train.log"


def run_id() -> str:
    return "qwen25_lora_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def tmux_has_session(name: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", name], capture_output=True, text=True, check=False).returncode == 0


def sanitized_tail(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return ""
    text = "\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:])
    token_key = "HF" + "_TOKEN"
    text = text.replace(os.environ.get(token_key, ""), "<redacted>") if os.environ.get(token_key) else text
    text = text.replace(str(Path.home()), "$HOME")
    return text


def write_train_report(payload: dict[str, Any]) -> Path:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "qwen_lora_train_report.json", payload)
    lines = [
        "# Qwen LoRA Train Report",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"training_status: {payload['training_status']}",
        f"tmux_session: {payload.get('tmux_session', 'none')}",
        f"log_path: {payload.get('log_path', 'none')}",
        f"gpu_used: {payload.get('gpu_used', 'not_available')}",
        f"candidate_adapter_path: {payload.get('candidate_adapter_path', 'not_registered_until_training_completes')}",
        f"eval_status: {payload.get('eval_status', 'not_started')}",
        f"registry_status: {payload.get('registry_status', 'not_candidate_yet')}",
        "promote: no",
    ]
    if payload.get("sanitized_tail_summary"):
        lines.extend(["", "## Sanitized Tail Summary", "```text", payload["sanitized_tail_summary"], "```"])
    path = reports / "qwen_lora_train_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_p0c2_status() -> None:
    status = build_status()
    write_status_report(status)


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch full QLoRA training in tmux and report running state.")
    parser.add_argument("--config", default="configs/train_qwen25_lora.yaml")
    parser.add_argument("--conda-env", default="aibola")
    parser.add_argument("--verify-seconds", type=int, default=90)
    args = parser.parse_args()
    preflight = choose_gpu("train")
    write_preflight_report(preflight, "data/outputs/reports/qwen_lora_gpu_train.env")
    if preflight["status"] != "ok":
        payload = {
            "status": "blocked",
            "blocked_reason": preflight["blocked_reason"],
            "training_status": "blocked",
            "gpu_used": "none",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "eval_status": "not_started",
            "registry_status": "not_candidate_yet",
            "promote": "no",
            "gpu_preflight": preflight,
        }
        write_train_report(payload)
        write_p0c2_status()
        print(json.dumps({"status": "blocked", "training_status": "blocked", "blocked_reason": payload["blocked_reason"]}, ensure_ascii=False))
        return 2
    rid = run_id()
    session = BASE_SESSION if not tmux_has_session(BASE_SESSION) else f"{BASE_SESSION}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    log_rel = "logs/qwen25_lora_train.log" if session == BASE_SESSION else f"logs/qwen25_lora_train_{rid}.log"
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
        + f"CUDA_VISIBLE_DEVICES={preflight['selected_gpu']} TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false "
        + f"python -m src.models.qwen25_lora_train --config {shlex.quote(args.config)} --run-id {shlex.quote(rid)} "
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
            "log_path": BASE_LOG if session == BASE_SESSION else "~/" + f"aibola/{log_rel}",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "eval_status": "not_started",
            "registry_status": "not_candidate_yet",
            "promote": "no",
            "tmux_error": launch.stderr[:500],
        }
        write_train_report(payload)
        write_p0c2_status()
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
            "log_path": BASE_LOG if session == BASE_SESSION else "~/" + f"aibola/{log_rel}",
            "candidate_adapter_path": "not_registered_until_training_completes",
            "candidate_output_dir": f"lora_weights/ebola_qwen25_step/candidates/{rid}",
            "eval_status": "not_started",
            "registry_status": "not_candidate_yet",
            "promote": "no",
            "gpu_preflight": preflight,
        }
        write_train_report(payload)
        write_p0c2_status()
        print(json.dumps({"status": "running", "training_status": "running", "tmux_session": session, "gpu_used": preflight["selected_gpu"]}, ensure_ascii=False))
        return 0
    report = {}
    report_path = Path("data/outputs/reports/qwen_lora_train_report.json")
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}
    if report.get("training_status") == "completed":
        write_p0c2_status()
        print(json.dumps({"status": "completed", "training_status": "completed", "tmux_session": session}, ensure_ascii=False))
        return 0
    payload = {
        "status": "failed",
        "blocked_reason": report.get("blocked_reason", "tmux_session_exited_during_launch_verification"),
        "training_status": "failed",
        "run_id": rid,
        "gpu_used": preflight["selected_gpu"],
        "tmux_session": session,
        "log_path": BASE_LOG if session == BASE_SESSION else "~/" + f"aibola/{log_rel}",
        "candidate_adapter_path": "not_registered_until_training_completes",
        "eval_status": "not_started",
        "registry_status": "not_candidate_yet",
        "promote": "no",
        "sanitized_tail_summary": sanitized_tail(log_path),
        "gpu_preflight": preflight,
    }
    write_train_report(payload)
    write_p0c2_status()
    print(json.dumps({"status": "failed", "training_status": "failed", "blocked_reason": payload["blocked_reason"], "tmux_session": session}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

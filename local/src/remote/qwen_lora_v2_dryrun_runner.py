from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.remote.qwen_lora_v2_gpu_preflight import write_v2_preflight_report
from src.remote.gpu_preflight import choose_gpu
from src.utils.io import write_json


REPORT = Path("data/outputs/reports/qwen_lora_v2_dryrun_report.json")


def load_report() -> dict:
    if not REPORT.exists():
        return {}
    try:
        return json.loads(REPORT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_blocked(reason: str, preflight: dict) -> None:
    payload = {
        "status": "blocked",
        "training_status": "blocked",
        "blocked_reason": reason,
        "dry_run": True,
        "gpu_preflight": preflight,
        "candidate_adapter_path": "not_registered_until_training_completes",
        "promote_allowed": False,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    write_json(REPORT, payload)
    (REPORT.with_suffix(".md")).write_text(
        "\n".join(
            [
                "# Qwen LoRA v2 Dry-run Report",
                "",
                "status: blocked",
                f"blocked_reason: {reason}",
                "dry_run: true",
                "candidate_registered: false",
                "promote_allowed: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def run_attempt(gpu: int, run_id: str, extra: list[str]) -> int:
    env = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": str(gpu),
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    cmd = [
        "python",
        "-m",
        "src.models.qwen25_lora_train",
        "--config",
        "configs/train_qwen25_lora_v2.yaml",
        "--dry-run",
        "--max-steps",
        "20",
        "--sample-limit",
        "64",
        "--run-id",
        run_id,
        "--variant",
        "v2b_rebalance_plus_prompt_instruction",
        "--report-basename",
        "qwen_lora_v2_dryrun_report",
        *extra,
    ]
    return subprocess.run(cmd, env=env, check=False).returncode


def main() -> int:
    preflight = choose_gpu("v2_dryrun")
    write_v2_preflight_report(preflight, "data/outputs/reports/qwen_lora_v2_gpu.env")
    if preflight["status"] != "ok":
        write_blocked(preflight["blocked_reason"], preflight)
        print(json.dumps({"status": "blocked", "blocked_reason": preflight["blocked_reason"]}, ensure_ascii=False))
        return 2
    gpu = int(preflight["selected_gpu"])
    base = "qwen25_lora_v2_dryrun_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    attempts = [
        (base, []),
        (base + "_seq2048", ["--max-seq-length", "2048"]),
        (base + "_seq2048_ga16", ["--max-seq-length", "2048", "--gradient-accumulation-steps", "16"]),
    ]
    for index, (run_id, extra) in enumerate(attempts):
        code = run_attempt(gpu, run_id, extra)
        report = load_report()
        if code == 0 and report.get("status") == "ok":
            report["oom_retry_attempts"] = index
            report["gpu_preflight"] = preflight
            report["candidate_registered"] = False
            report["promote_allowed"] = False
            write_json(REPORT, report)
            print(json.dumps({"status": "ok", "dry_run_status": "ok", "gpu": gpu, "oom_retry_attempts": index}, ensure_ascii=False))
            return 0
        if report.get("blocked_reason") != "cuda_oom":
            print(json.dumps({"status": "blocked", "blocked_reason": report.get("blocked_reason", "dryrun_failed")}, ensure_ascii=False))
            return 2
    report = load_report()
    report["status"] = "blocked"
    report["training_status"] = "blocked"
    report["blocked_reason"] = "cuda_oom_after_seq2048_ga16_retry"
    report["gpu_preflight"] = preflight
    write_json(REPORT, report)
    print(json.dumps({"status": "blocked", "blocked_reason": report["blocked_reason"]}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

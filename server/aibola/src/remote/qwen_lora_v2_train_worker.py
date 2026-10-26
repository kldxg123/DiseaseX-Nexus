from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from src.utils.io import write_json


REPORT = Path("data/outputs/reports/qwen_lora_v2_train_report.json")


def load_report() -> dict:
    if not REPORT.exists():
        return {}
    try:
        return json.loads(REPORT.read_text(encoding="utf-8"))
    except Exception:
        return {}


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
        "--run-id",
        run_id,
        "--variant",
        "v2b_rebalance_plus_prompt_instruction",
        "--report-basename",
        "qwen_lora_v2_train_report",
        *extra,
    ]
    return subprocess.run(cmd, env=env, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full Qwen LoRA v2 training with OOM retry policy.")
    parser.add_argument("--gpu", required=True, type=int)
    parser.add_argument("--run-id-base", required=True)
    args = parser.parse_args()
    attempts = [
        (args.run_id_base, []),
        (args.run_id_base + "_seq2048", ["--max-seq-length", "2048"]),
        (args.run_id_base + "_seq2048_ga16", ["--max-seq-length", "2048", "--gradient-accumulation-steps", "16"]),
    ]
    for index, (run_id, extra) in enumerate(attempts):
        code = run_attempt(args.gpu, run_id, extra)
        report = load_report()
        if code == 0 and report.get("training_status") == "completed":
            report["oom_retry_attempts"] = index
            report["training_status"] = "completed"
            report["registry_status"] = "not_candidate_until_eval_and_compare_complete"
            report["promote_allowed"] = False
            report["allow_auto_promote"] = False
            write_json(REPORT, report)
            print(json.dumps({"status": "ok", "training_status": "completed", "run_id": run_id}, ensure_ascii=False))
            return 0
        if report.get("blocked_reason") != "cuda_oom":
            print(json.dumps({"status": "blocked", "training_status": report.get("training_status", "blocked"), "blocked_reason": report.get("blocked_reason", "training_failed")}, ensure_ascii=False))
            return 2
    report = load_report()
    report["status"] = "blocked"
    report["training_status"] = "blocked"
    report["blocked_reason"] = "cuda_oom_after_seq2048_ga16_retry"
    report["registry_status"] = "not_candidate_yet"
    report["promote_allowed"] = False
    report["allow_auto_promote"] = False
    write_json(REPORT, report)
    print(json.dumps({"status": "blocked", "training_status": "blocked", "blocked_reason": report["blocked_reason"]}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

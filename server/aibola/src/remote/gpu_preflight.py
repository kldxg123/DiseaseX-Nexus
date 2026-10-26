from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from src.utils.io import ensure_parent, write_json


MEMORY_BUSY_MB = 8192
UTIL_BUSY_PCT = 20


def query_gpus() -> list[dict[str, Any]]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError("nvidia_smi_failed")
    gpus = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        gpus.append(
            {
                "index": int(parts[0]),
                "name": parts[1],
                "memory_total_mb": int(float(parts[2])),
                "memory_used_mb": int(float(parts[3])),
                "utilization_gpu_pct": int(float(parts[4])),
            }
        )
    return gpus


def is_idle(gpu: dict[str, Any]) -> bool:
    return int(gpu["memory_used_mb"]) <= MEMORY_BUSY_MB and int(gpu["utilization_gpu_pct"]) <= UTIL_BUSY_PCT


def choose_gpu(stage: str) -> dict[str, Any]:
    try:
        gpus = query_gpus()
    except Exception as exc:
        return {
            "status": "blocked",
            "blocked_reason": "nvidia_smi_failed",
            "stage": stage,
            "error_type": type(exc).__name__,
            "selected_gpu": None,
            "gpus": [],
        }
    selected = None
    gpu0_busy = False
    for gpu in gpus:
        if gpu["index"] == 0:
            gpu0_busy = not is_idle(gpu)
            if not gpu0_busy:
                selected = gpu
            break
    if selected is None:
        for gpu in sorted(gpus, key=lambda item: item["index"]):
            if gpu["index"] == 0:
                continue
            if is_idle(gpu):
                selected = gpu
                break
    if selected is None:
        return {
            "status": "blocked",
            "blocked_reason": "GPU 0 is busy" if gpu0_busy else "no idle single GPU available",
            "stage": stage,
            "selected_gpu": None,
            "gpus": gpus,
            "memory_busy_threshold_mb": MEMORY_BUSY_MB,
            "util_busy_threshold_pct": UTIL_BUSY_PCT,
        }
    return {
        "status": "ok",
        "blocked_reason": "none",
        "stage": stage,
        "selected_gpu": selected["index"],
        "gpu0_busy": gpu0_busy,
        "selected_gpu_name": selected["name"],
        "selected_gpu_memory_used_mb": selected["memory_used_mb"],
        "selected_gpu_utilization_pct": selected["utilization_gpu_pct"],
        "gpus": gpus,
        "memory_busy_threshold_mb": MEMORY_BUSY_MB,
        "util_busy_threshold_pct": UTIL_BUSY_PCT,
    }


def write_preflight_report(payload: dict[str, Any], env_file: str | None = None) -> None:
    stage = payload["stage"]
    reports = Path("data/outputs/reports")
    reports.mkdir(parents=True, exist_ok=True)
    write_json(reports / f"qwen_lora_gpu_preflight_{stage}.json", payload)
    lines = [
        f"# Qwen LoRA GPU Preflight {stage}",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"selected_gpu: {payload.get('selected_gpu')}",
        f"gpu0_busy: {str(payload.get('gpu0_busy', False)).lower()}",
    ]
    (reports / f"qwen_lora_gpu_preflight_{stage}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if env_file:
        env_path = ensure_parent(env_file)
        selected = payload.get("selected_gpu")
        env_path.write_text(f"GPU_INDEX={selected}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Choose one idle GPU for QLoRA dry-run or training.")
    parser.add_argument("--stage", required=True, choices=["dryrun", "train"])
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()
    payload = choose_gpu(args.stage)
    write_preflight_report(payload, args.env_file)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

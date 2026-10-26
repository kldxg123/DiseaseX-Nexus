from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.remote.gpu_preflight import choose_gpu
from src.utils.io import ensure_parent, write_json


def write_v2_preflight_report(payload: dict, env_file: str | None = None) -> None:
    reports = Path("data/outputs/reports")
    reports.mkdir(parents=True, exist_ok=True)
    write_json(reports / "qwen_lora_v2_gpu_preflight.json", payload)
    lines = [
        "# Qwen LoRA v2 GPU Preflight",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"stage: {payload.get('stage')}",
        f"selected_gpu: {payload.get('selected_gpu')}",
        f"selected_gpu_name: {payload.get('selected_gpu_name')}",
        f"selected_gpu_memory_used_mb: {payload.get('selected_gpu_memory_used_mb')}",
        f"selected_gpu_utilization_pct: {payload.get('selected_gpu_utilization_pct')}",
        f"gpu0_busy: {str(payload.get('gpu0_busy', False)).lower()}",
        "single_gpu_only: true",
        "fsdp: disabled",
        "deepspeed: disabled",
        "",
        "## GPUs",
        "```json",
        json.dumps(payload.get("gpus", []), ensure_ascii=False, indent=2),
        "```",
    ]
    (reports / "qwen_lora_v2_gpu_preflight.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if env_file:
        env_path = ensure_parent(env_file)
        env_path.write_text(f"GPU_INDEX={payload.get('selected_gpu')}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Choose one idle GPU for Qwen LoRA v2.")
    parser.add_argument("--stage", default="train")
    parser.add_argument("--env-file", default="data/outputs/reports/qwen_lora_v2_gpu.env")
    args = parser.parse_args()
    payload = choose_gpu(f"v2_{args.stage}")
    write_v2_preflight_report(payload, args.env_file)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

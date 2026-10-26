from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run Qwen QLoRA candidate-v2 GPU preflight.")
    command = (
        "python -m src.remote.qwen_lora_v2_gpu_preflight "
        "--stage train --env-file data/outputs/reports/qwen_lora_v2_gpu.env"
    )
    return run_stage(P0c1Stage("run_qwen_lora_v2_gpu_preflight", command, timeout=300), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Prepare balanced Qwen LoRA dataset on remote.")
    return run_stage(
        P0c1Stage(
            "run_qwen_lora_dataset_prepare",
            "python -m src.models.qwen25_lora_dataset --config configs/train_qwen25_lora.yaml",
            timeout=900,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

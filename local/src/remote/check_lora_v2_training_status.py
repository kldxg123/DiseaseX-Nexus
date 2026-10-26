from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Check Qwen LoRA candidate-v2 training status.")
    return run_stage(
        P0c1Stage("check_lora_v2_training_status", "python -m src.remote.qwen_lora_v2_training_status", timeout=300),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

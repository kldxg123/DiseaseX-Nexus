from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Evaluate completed Qwen LoRA candidate-v2 adapter.")
    return run_stage(
        P0c1Stage("run_qwen_lora_v2_eval", "python -m src.remote.qwen_lora_v2_eval_runner", timeout=14400),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

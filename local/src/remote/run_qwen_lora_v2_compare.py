from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Compare Qwen LoRA candidate-v2 with accepted references.")
    return run_stage(
        P0c1Stage("run_qwen_lora_v2_compare", "python -m src.eval.qwen_lora_v2_compare", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

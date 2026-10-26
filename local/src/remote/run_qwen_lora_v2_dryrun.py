from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run Qwen QLoRA candidate-v2 dry-run on one idle GPU.")
    return run_stage(
        P0c1Stage("run_qwen_lora_v2_dryrun", "python -m src.remote.qwen_lora_v2_dryrun_runner", timeout=7200),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P0c-4B candidate-v2 training plan on remote without training.")
    return run_stage(
        P0c1Stage(
            "run_qwen_candidate_v2_training_plan",
            "python -m src.reports.qwen_candidate_v2_training_plan --config configs/train_qwen25_lora_v2.yaml",
            timeout=600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Build P0c-4B candidate-v2 SFT dataset on remote.")
    return run_stage(
        P0c1Stage(
            "run_qwen_candidate_v2_dataset_builder",
            "python -m src.models.qwen25_candidate_v2_dataset_builder --config configs/train_qwen25_lora_v2.yaml",
            timeout=1800,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

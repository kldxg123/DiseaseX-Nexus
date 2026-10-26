from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Evaluate completed Qwen LoRA candidate on remote.")
    return run_stage(
        P0c1Stage(
            "run_qwen_lora_eval",
            "CUDA_VISIBLE_DEVICES=2 TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false "
            "python -m src.models.qwen25_lora_eval --config configs/train_qwen25_lora.yaml",
            timeout=7200,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


COMMAND = (
    "CUDA_VISIBLE_DEVICES=2 TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false "
    "python -m src.eval.qwen_error_analysis --config configs/train_qwen25_lora.yaml --adapter-id qwen25_lora_20260623_011633"
)


def main() -> int:
    args = parse_args("Run P0c-3 Qwen error analysis on remote.")
    return run_stage(P0c1Stage("run_qwen_error_analysis", COMMAND, timeout=14400), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

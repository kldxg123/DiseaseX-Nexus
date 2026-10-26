from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Evaluate Qwen LoRA v1 on P0e external test.")
    command = (
        "CUDA_VISIBLE_DEVICES=2 TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 "
        "TOKENIZERS_PARALLELISM=false python -m src.eval.external_qwen_eval --version v1"
    )
    return run_stage(P0c1Stage("run_external_qwen_v1_eval", command, timeout=14400), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

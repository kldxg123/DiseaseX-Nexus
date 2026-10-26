from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


DRYRUN_COMMAND = r"""
python -m src.remote.gpu_preflight --stage dryrun --env-file data/outputs/reports/qwen_lora_gpu_dryrun.env &&
. data/outputs/reports/qwen_lora_gpu_dryrun.env &&
CUDA_VISIBLE_DEVICES="$GPU_INDEX" TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false python -m src.models.qwen25_lora_train --config configs/train_qwen25_lora.yaml --dry-run --max-steps 20 --sample-limit 64
"""


def main() -> int:
    args = parse_args("Run Qwen QLoRA dry-run on one idle GPU.")
    return run_stage(P0c1Stage("run_qwen_lora_dryrun", " ".join(DRYRUN_COMMAND.split()), timeout=7200), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

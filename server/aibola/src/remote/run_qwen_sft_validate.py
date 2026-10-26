from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Validate Qwen SFT schema for P0c-1.")
    return run_stage(P0c1Stage("run_qwen_sft_validate", "python -m src.models.qwen25_sft_validate --config configs/train_qwen25_lora.yaml", timeout=900), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

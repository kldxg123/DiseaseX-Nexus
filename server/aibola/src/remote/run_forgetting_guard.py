from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P0c-3 forgetting guard on remote.")
    return run_stage(
        P0c1Stage(
            "run_forgetting_guard",
            "python -m src.step.forgetting_guard --config configs/config.yaml --adapter-id qwen25_lora_20260623_011633",
            timeout=600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

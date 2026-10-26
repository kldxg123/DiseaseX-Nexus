from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Initialize STEP experience buffer from P0c-3 Qwen eval samples on remote.")
    return run_stage(
        P0c1Stage(
            "run_step_experience_init",
            "python -m src.step.experience_buffer --init-from-qwen-eval --config configs/config.yaml --adapter-id qwen25_lora_20260623_011633",
            timeout=900,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

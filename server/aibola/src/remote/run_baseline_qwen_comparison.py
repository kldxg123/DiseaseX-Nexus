from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P0a/Qwen comparison on remote.")
    return run_stage(
        P0c1Stage("run_baseline_qwen_comparison", "python -m src.eval.baseline_qwen_comparison", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

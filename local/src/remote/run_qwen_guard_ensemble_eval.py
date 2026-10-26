from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P0c-4A Qwen guard/ensemble eval on remote.")
    return run_stage(P0c1Stage("run_qwen_guard_ensemble_eval", "python -m src.eval.qwen_guard_ensemble_eval", timeout=1800), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P0c-4A same-split baseline eval on remote.")
    return run_stage(P0c1Stage("run_same_split_baseline_eval", "python -m src.eval.same_split_baseline_eval", timeout=3600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P0c-4A up recall slice analysis on remote.")
    return run_stage(P0c1Stage("run_up_recall_slice_analysis", "python -m src.eval.up_recall_slice_analysis", timeout=900), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

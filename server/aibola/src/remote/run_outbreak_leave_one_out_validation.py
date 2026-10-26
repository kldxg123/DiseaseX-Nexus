from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P2a outbreak leave-one-out validation on remote.")
    return run_stage(P0c1Stage("run_outbreak_leave_one_out_validation", "python -m src.eval.outbreak_leave_one_out_validation", timeout=900), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

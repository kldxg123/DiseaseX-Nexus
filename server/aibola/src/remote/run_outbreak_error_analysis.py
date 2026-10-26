from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P2a outbreak risk error analysis on remote.")
    return run_stage(P0c1Stage("run_outbreak_error_analysis", "python -m src.eval.outbreak_risk_error_analysis", timeout=900), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

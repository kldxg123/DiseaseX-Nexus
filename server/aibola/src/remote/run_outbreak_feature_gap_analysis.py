from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P2b outbreak feature gap analysis on remote.")
    return run_stage(P0c1Stage("run_outbreak_feature_gap_analysis", "python -m src.reports.outbreak_feature_gap_analysis", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())


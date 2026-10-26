from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P2a direct public data collection on remote.")
    return run_stage(P0c1Stage("run_outbreak_direct_data_collection", "python -m src.data_acquisition.outbreak_risk_collectors direct", timeout=1200), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

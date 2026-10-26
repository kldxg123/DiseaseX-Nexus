from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Build P2a outbreak event table on remote.")
    return run_stage(P0c1Stage("run_outbreak_event_table_builder", "python -m src.data.outbreak_risk_dataset event_table", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

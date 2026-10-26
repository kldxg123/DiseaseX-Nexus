from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Build P2a region-week panel on remote.")
    return run_stage(P0c1Stage("run_outbreak_region_week_panel_builder", "python -m src.data.outbreak_risk_dataset panel", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

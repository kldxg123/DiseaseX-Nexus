from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P2b supervisor-ready outbreak risk report on remote.")
    return run_stage(P0c1Stage("run_outbreak_risk_supervisor_report", "python -m src.reports.outbreak_risk_supervisor_report", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())


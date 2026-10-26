from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate remote P0c-1 status report.")
    return run_stage(P0c1Stage("run_p0c1_status", "python -m src.reports.p0c1_status --config configs/config.yaml", timeout=300), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

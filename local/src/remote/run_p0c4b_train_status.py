from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P0c-4B train status report.")
    return run_stage(
        P0c1Stage("run_p0c4b_train_status", "python -m src.reports.p0c4b_train_status", timeout=300),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

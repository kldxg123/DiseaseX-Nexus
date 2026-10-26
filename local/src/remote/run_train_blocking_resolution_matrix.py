from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P1c train-blocking resolution matrix on remote.")
    return run_stage(
        P0c1Stage(
            "run_train_blocking_resolution_matrix",
            "python -m src.reports.train_blocking_resolution_matrix",
            timeout=600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

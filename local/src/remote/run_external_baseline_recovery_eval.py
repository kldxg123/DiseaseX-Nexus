from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Evaluate recovered traditional ML baseline on P0e external data.")
    return run_stage(
        P0c1Stage("run_external_baseline_recovery_eval", "python -m src.eval.external_baseline_recovery_eval", timeout=900),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Recover or rebuild traditional ML baseline artifacts for P0f.")
    return run_stage(
        P0c1Stage("run_baseline_artifact_recovery", "python -m src.eval.baseline_artifact_recovery --config configs/config.yaml", timeout=1800),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

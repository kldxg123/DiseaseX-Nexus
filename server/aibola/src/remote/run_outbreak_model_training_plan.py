from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P2a outbreak model training plan on remote.")
    return run_stage(
        P0c1Stage(
            "run_outbreak_model_training_plan",
            "python -m src.reports.outbreak_prediction_feasibility --report model_training_plan",
            timeout=600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

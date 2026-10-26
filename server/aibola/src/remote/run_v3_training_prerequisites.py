from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P0f v3 training prerequisites on remote.")
    return run_stage(
        P0c1Stage(
            "run_v3_training_prerequisites",
            "python -m src.reports.p0f_final_review --report v3_training_prerequisites",
            timeout=600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

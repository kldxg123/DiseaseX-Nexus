from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P0f external model selection review on remote.")
    return run_stage(
        P0c1Stage(
            "run_external_model_selection_review",
            "python -m src.reports.p0f_final_review --report external_model_selection_review",
            timeout=600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

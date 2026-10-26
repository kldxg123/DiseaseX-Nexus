from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P0c-5 model selection report on remote.")
    return run_stage(
        P0c1Stage("run_model_selection_report", "python -m src.reports.p0c5_final_review --report model_selection_report", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

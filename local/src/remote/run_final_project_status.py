from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P0c-5 final project status on remote.")
    return run_stage(
        P0c1Stage("run_final_project_status", "python -m src.reports.p0c5_final_review --report final_project_status", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

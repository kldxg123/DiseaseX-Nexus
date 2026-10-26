from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate DRC manual case completion pack on remote.")
    return run_stage(
        P0c1Stage("run_drc_manual_case_completion_pack", "python -m src.reports.drc_manual_case_completion_pack", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

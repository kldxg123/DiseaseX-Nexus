from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate filled template validation status on remote.")
    return run_stage(
        P0c1Stage("run_filled_template_validation_status", "python -m src.reports.filled_template_validation_status", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

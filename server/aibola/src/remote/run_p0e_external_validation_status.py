from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Build P0e external validation status report.")
    return run_stage(
        P0aStage("run_p0e_external_validation_status", "python -m src.reports.external_validation_status", timeout=900),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Analyze P0e external validation errors.")
    return run_stage(
        P0aStage("run_external_error_analysis", "python -m src.eval.external_error_analysis", timeout=900),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

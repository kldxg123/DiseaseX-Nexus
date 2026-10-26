from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote P0a quality report.")
    return run_stage(
        P0aStage("run_quality_report", "python -m src.data.quality_report --config configs/config.yaml", timeout=900),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

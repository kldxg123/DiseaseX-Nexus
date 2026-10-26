from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote P0a feature builder.")
    return run_stage(
        P0aStage("run_feature_builder", "python -m src.processors.feature_builder --config configs/config.yaml", timeout=1200),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

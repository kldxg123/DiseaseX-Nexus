from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote P0d external feature builder.")
    return run_stage(
        P0aStage("run_external_feature_builder", "python -m src.processors.external_feature_builder --config configs/config.yaml", timeout=1800),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

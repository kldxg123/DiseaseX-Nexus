from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Build remote P0e external validation datasets.")
    return run_stage(
        P0aStage("run_external_validation_dataset_builder", "python -m src.eval.external_validation_dataset_builder", timeout=900),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

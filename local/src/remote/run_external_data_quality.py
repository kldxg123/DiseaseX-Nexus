from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote P0d external data quality gate.")
    return run_stage(
        P0aStage("run_external_data_quality", "python -m src.data.external_data_quality --config configs/config.yaml", timeout=900),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote P0a data audit.")
    return run_stage(
        P0aStage("run_data_audit", "python -m src.data.audit_current_data --config configs/config.yaml", timeout=900),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Audit fixed P2a external-test errors on remote.")
    return run_stage(P0c1Stage("run_outbreak_external_error_audit", "python -m src.eval.outbreak_external_error_audit", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())


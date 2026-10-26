from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Audit P2a outbreak dataset and splits on remote.")
    return run_stage(P0c1Stage("run_outbreak_dataset_audit", "python -m src.eval.outbreak_dataset_audit", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())


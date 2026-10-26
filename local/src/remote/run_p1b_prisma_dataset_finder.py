from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run P1b PRISMA literature dataset finder on remote.")
    return run_stage(P0aStage("run_p1b_prisma_dataset_finder", "python -m src.data_acquisition.prisma_literature_dataset_finder", timeout=1200), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P2a prototype final review on remote.")
    return run_stage(P0c1Stage("run_p2a_prototype_final_review", "python -m src.reports.p2a_prototype_final_review", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())


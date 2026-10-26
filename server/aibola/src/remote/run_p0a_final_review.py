from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate remote P0a final review for P0c-1.")
    return run_stage(P0c1Stage("run_p0a_final_review", "python -m src.reports.p0a_final_review --config configs/config.yaml", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

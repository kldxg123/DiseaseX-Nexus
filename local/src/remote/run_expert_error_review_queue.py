from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate expert error review queue on remote.")
    return run_stage(
        P0c1Stage("run_expert_error_review_queue", "python -m src.reports.expert_error_review_queue", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate preference pair annotation queue on remote.")
    return run_stage(
        P0c1Stage("run_preference_pair_annotation_queue", "python -m src.reports.preference_pair_annotation_queue", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

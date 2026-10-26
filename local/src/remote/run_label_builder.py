from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote P0a label builder.")
    return run_stage(
        P0aStage(
            "run_label_builder",
            "python -m src.processors.label_builder --config configs/config.yaml --horizons 7 14 21 30",
            timeout=1200,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

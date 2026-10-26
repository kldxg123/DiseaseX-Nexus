from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote P0a backtest.")
    return run_stage(
        P0aStage(
            "run_backtest",
            "python -m src.eval.backtest --config configs/config.yaml --horizons 7 14 21 30",
            timeout=3600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

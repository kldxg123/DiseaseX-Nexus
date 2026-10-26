from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Validate filled manual templates on remote.")
    return run_stage(
        P0c1Stage("run_filled_template_validator", "python -m src.data.filled_template_validator", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

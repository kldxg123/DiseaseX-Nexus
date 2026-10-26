from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Generate P0c-3 status report on remote.")
    return run_stage(
        P0c1Stage("run_p0c3_status", "python -m src.reports.p0c3_status --adapter-id qwen25_lora_20260623_011633", timeout=600),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

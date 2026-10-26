from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Download P1b case sources on remote.")
    return run_stage(P0aStage("run_p1b_download_case_sources", "python -m src.data_acquisition.download_orchestrator case_sources", timeout=3600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

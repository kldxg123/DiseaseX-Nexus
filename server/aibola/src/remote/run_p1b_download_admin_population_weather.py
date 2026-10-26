from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Download P1b admin, population, and weather sources on remote.")
    return run_stage(P0aStage("run_p1b_download_admin_population_weather", "python -m src.data_acquisition.download_orchestrator admin_population_weather", timeout=5400), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

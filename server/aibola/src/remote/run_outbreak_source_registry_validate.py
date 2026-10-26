from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Validate P2a outbreak source registry on remote.")
    return run_stage(P0c1Stage("run_outbreak_source_registry_validate", "python -m src.data_acquisition.outbreak_risk_collectors validate", timeout=600), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

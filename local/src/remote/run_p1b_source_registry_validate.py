from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Validate P1b source registry on remote.")
    return run_stage(P0aStage("run_p1b_source_registry_validate", "python -m src.data_acquisition.source_registry", timeout=300), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

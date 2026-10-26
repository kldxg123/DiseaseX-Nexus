from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Initialize remote adapter registry for P0c-1.")
    return run_stage(P0c1Stage("run_adapter_registry_init", "python -m src.step.adapter_registry --init --config configs/config.yaml", timeout=300), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Update P0f external validation final-review registry hold fields on remote.")
    return run_stage(
        P0c1Stage(
            "run_adapter_registry_update",
            "python -m src.step.adapter_registry --update-p0f-final-review --config configs/config.yaml",
            timeout=600,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

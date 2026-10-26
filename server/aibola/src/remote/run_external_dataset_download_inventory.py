from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote external dataset download inventory.")
    return run_stage(
        P0aStage(
            "run_external_dataset_download_inventory",
            "python -m src.data.external_dataset_download_inventory --raw-dir data/raw/external_selected",
            timeout=900,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

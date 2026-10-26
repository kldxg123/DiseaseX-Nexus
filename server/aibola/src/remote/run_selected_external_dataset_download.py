from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Download selected public external datasets on remote.")
    return run_stage(
        P0aStage(
            "run_selected_external_dataset_download",
            "python -m src.collectors.selected_external_dataset_downloader --max-file-mb 850",
            timeout=7200,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

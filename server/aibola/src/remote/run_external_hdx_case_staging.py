from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote HDX case observation staging.")
    return run_stage(
        P0aStage(
            "run_external_hdx_case_staging",
            "python -m src.data.external_hdx_case_staging --raw-hdx-dir data/raw/external_selected/hdx",
            timeout=900,
        ),
        args.root,
        args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

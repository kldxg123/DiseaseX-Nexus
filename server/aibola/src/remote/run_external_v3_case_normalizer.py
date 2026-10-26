from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run external-v3 case normalizer on remote.")
    return run_stage(P0aStage("run_external_v3_case_normalizer", "python -m src.data.external_v3_case_normalizer", timeout=900), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

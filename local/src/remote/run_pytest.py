from __future__ import annotations

from src.remote.p0a_runner import P0aStage, parse_args, run_stage


def main() -> int:
    args = parse_args("Run remote pytest for P0a.")
    return run_stage(P0aStage("run_pytest", "pytest -q", timeout=900), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

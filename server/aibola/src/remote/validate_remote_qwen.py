from __future__ import annotations

from src.remote.p0c1_runner import P0c1Stage, parse_args, run_stage


def main() -> int:
    args = parse_args("Validate remote Qwen model in local-only mode.")
    return run_stage(P0c1Stage("validate_remote_qwen", "python -m src.models.qwen25_model_validate --model-dir models/Qwen2.5-7B-Instruct", timeout=1200), args.root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

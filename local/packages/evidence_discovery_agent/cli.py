from __future__ import annotations

import argparse
import json

from evidence_discovery_agent.report import run_mock_evidence_discovery


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Milestone 2 evidence discovery readiness CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run-mock-evidence-discovery")
    run.add_argument("--disease", required=True)
    run.add_argument("--country", required=True)
    run.add_argument("--target", required=True)
    run.add_argument("--horizon-days", type=int, default=28)
    run.add_argument("--output-dir", default="reports/milestone2_readiness")
    args = parser.parse_args(argv)

    if args.command == "run-mock-evidence-discovery":
        result = run_mock_evidence_discovery(
            disease=args.disease,
            country=args.country,
            target=args.target,
            horizon_days=args.horizon_days,
            output_dir=args.output_dir,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") == "ok" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

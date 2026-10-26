from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.db import get_session, init_db
from source_discovery_agent.bridge_report import bridge_result_to_dict
from source_discovery_agent.fixture_mapping import infer_fixture_for_parser_strategy
from source_discovery_agent.generic_bridge import DiscoveryParserBridge, DiscoveryParserBridgeInput


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Source Discovery to Generic Parser bridge with local fixtures.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run-fixture-bridge")
    run_parser.add_argument("--database-url", required=True)
    run_parser.add_argument("--country", default="United States")
    run_parser.add_argument("--disease", default="influenza")
    run_parser.add_argument("--parser-strategy", required=True)
    run_parser.add_argument("--no-build-features", action="store_true")
    args = parser.parse_args(argv)

    if args.command != "run-fixture-bridge":
        return 2

    mapping = infer_fixture_for_parser_strategy(
        parser_strategy=args.parser_strategy,
        disease=args.disease,
    )
    fixture_content = Path(mapping["fixture_path"]).read_text(encoding="utf-8")
    init_db(args.database_url).dispose()
    with get_session(args.database_url) as session:
        result = DiscoveryParserBridge(session).run(
            DiscoveryParserBridgeInput(
                country=args.country,
                disease=args.disease,
                source_candidate_name=str(mapping["source_name"]),
                parser_strategy=args.parser_strategy,
                fixture_content=fixture_content,
                fixture_url=str(mapping["fixture_url"]),
                source_name=str(mapping["source_name"]),
                source_type=str(mapping["source_type"]),
                field_mapping=mapping.get("field_mapping"),
                records_path=mapping.get("records_path"),
            ),
            build_features=not args.no_build_features,
        )
    print(json.dumps(bridge_result_to_dict(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.status in {"ok", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json

from common.db import get_session, init_db
from common.models import SourceRegistry
from crawler_optimizer_agent.connector_spec import (
    connector_spec_to_markdown,
    infer_connector_spec_from_source,
    safe_connector_spec_summary,
)
from crawler_optimizer_agent.optimizer_agent import CrawlerOptimizerAgent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local crawler optimization agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--database-url", required=True)
    analyze.add_argument("--country", default=None)
    analyze.add_argument("--disease", default=None)
    analyze.add_argument("--limit-sources", type=int, default=100)
    analyze.add_argument("--limit-documents", type=int, default=200)
    analyze.add_argument("--apply-safe-actions", action="store_true")

    analyze_source = subparsers.add_parser("analyze-source")
    analyze_source.add_argument("--database-url", required=True)
    analyze_source.add_argument("--source-id", required=True)
    analyze_source.add_argument("--country", default=None)
    analyze_source.add_argument("--disease", default=None)

    connector_spec = subparsers.add_parser("connector-spec")
    connector_spec.add_argument("--database-url", required=True)
    connector_spec.add_argument("--source-id", required=True)
    connector_spec.add_argument("--markdown", action="store_true")

    args = parser.parse_args(argv)
    init_db(args.database_url).dispose()
    with get_session(args.database_url) as session:
        if args.command == "analyze":
            report = CrawlerOptimizerAgent(session).analyze(
                country=args.country,
                disease=args.disease,
                limit_sources=args.limit_sources,
                limit_documents=args.limit_documents,
                apply_safe_actions=args.apply_safe_actions,
            )
            print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "analyze-source":
            report = CrawlerOptimizerAgent(session).analyze_source(
                args.source_id,
                country=args.country,
                disease=args.disease,
            )
            print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "connector-spec":
            source = session.get(SourceRegistry, int(args.source_id))
            if source is None:
                print(json.dumps({"status": "not_found", "source_id": args.source_id}, sort_keys=True))
                return 1
            spec = infer_connector_spec_from_source(source)
            if args.markdown:
                print(connector_spec_to_markdown(spec))
            else:
                print(json.dumps(safe_connector_spec_summary(spec), ensure_ascii=False, sort_keys=True))
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

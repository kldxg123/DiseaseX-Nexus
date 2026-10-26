from __future__ import annotations

import argparse
import json

from deepseek_web_search_agent.approval_ingestion import ingest_approved_table_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest approved DeepSeek table review rows into a pilot DB.")
    parser.add_argument("--review-table", required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--allow-persistence", action="store_true")
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    result = ingest_approved_table_rows(
        review_table_path=args.review_table,
        database_url=args.database_url,
        allow_persistence=bool(args.allow_persistence),
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "approved_rows_ingested": result.get("approved_rows_ingested", 0),
                "epidemic_events_created": result.get("epidemic_events_created", 0),
                "feature_generation_allowed": result.get("feature_generation_allowed", False),
                "paths": result.get("paths", {}),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.get("status") in {"ok", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

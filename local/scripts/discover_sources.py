from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
if str(PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGES_DIR))

from common.config import get_settings
from common.db import get_session, init_db
from crawler_agent.access_policy import load_access_policies
from crawler_agent.gdelt import build_gdelt_url, parse_gdelt_articles
from crawler_agent.live_fetch_guard import LiveFetchGuard
from crawler_agent.source_discovery import GdeltDiscoveryService
from crawler_agent.source_registry import SourceRegistryService


def _summary(candidates) -> dict:
    return {
        "candidate_count": len(candidates),
        "candidates": [
            {
                "name": candidate.name,
                "base_url": candidate.base_url,
                "status": candidate.source_payload["status"],
                "article_count": candidate.article_count,
            }
            for candidate in candidates
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover candidate sources from GDELT metadata.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--country", default=None)
    parser.add_argument("--disease", required=True)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--fixture-json", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--source-policy", default="configs/source_access_policy.example.json")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--robots-allowed", action="store_true")
    args = parser.parse_args(argv)

    query_url = build_gdelt_url(
        country=args.country,
        disease=args.disease,
        lookback_days=args.lookback_days,
    )
    database_url = args.database_url or get_settings().database_url

    if args.fixture_json:
        payload = Path(args.fixture_json).read_text(encoding="utf-8")
        articles = parse_gdelt_articles(payload)
        if args.dry_run:
            with get_session("sqlite:///:memory:") as session:
                service = GdeltDiscoveryService(session)
                candidates = service.discover_candidates_from_articles(
                    articles=articles,
                    country=args.country,
                    disease=args.disease,
                    query_url=query_url,
                )
        else:
            init_db(database_url).dispose()
            with get_session(database_url) as session:
                service = GdeltDiscoveryService(session)
                candidates = service.discover_candidates_from_articles(
                    articles=articles,
                    country=args.country,
                    disease=args.disease,
                    query_url=query_url,
                )
                registry = SourceRegistryService(session)
                for candidate in candidates:
                    registry.upsert_source(**candidate.source_payload)
        print(json.dumps(_summary(candidates), ensure_ascii=False, sort_keys=True))
        return 0

    if args.dry_run:
        print(json.dumps({"dry_run": True, "query_url": query_url}, sort_keys=True))
        return 0

    if args.allow_network:
        guard = LiveFetchGuard(load_access_policies(args.source_policy))
        decision = guard.check(
            "gdelt",
            allow_network=args.allow_network,
            requested_url=query_url,
            robots_allowed=True if args.robots_allowed else None,
            terms_reviewed=args.terms_reviewed,
            manual_reviewed=args.manual_reviewed,
        )
        print(json.dumps({"decision": _decision_summary(decision)}, ensure_ascii=False, sort_keys=True))
        if not decision.allowed:
            raise SystemExit("Live GDELT discovery denied by source access policy.")
        raise SystemExit("Live fetch not implemented/enabled in this MVP.")

    raise SystemExit("Refusing live GDELT discovery without a local fixture.")


def _decision_summary(decision) -> dict:
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "warnings": decision.warnings,
    }


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
if str(PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGES_DIR))

from crawler_agent.access_policy import load_access_policies
from crawler_agent.live_fetch_guard import LiveFetchGuard
from crawler_agent.url_utils import normalize_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local crawler MVP skeleton.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--source-id", type=int, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--source-key", default="manual_url")
    parser.add_argument("--source-policy", default="configs/source_access_policy.example.json")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--robots-allowed", action="store_true")
    args = parser.parse_args(argv)

    normalized_url = normalize_url(args.url)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "source_id": args.source_id}, sort_keys=True))
        return 0
    if not args.allow_network:
        raise SystemExit("Refusing to fetch without --allow-network.")

    guard = LiveFetchGuard(load_access_policies(args.source_policy))
    decision = guard.check(
        args.source_key,
        allow_network=args.allow_network,
        requested_url=normalized_url,
        robots_allowed=True if args.robots_allowed else None,
        terms_reviewed=args.terms_reviewed,
        manual_reviewed=args.manual_reviewed,
    )
    print(json.dumps({"decision": _decision_summary(decision)}, ensure_ascii=False, sort_keys=True))
    if not decision.allowed:
        raise SystemExit("Live fetch denied by source access policy.")
    raise SystemExit("Live fetch not implemented/enabled in this MVP.")


def _decision_summary(decision) -> dict:
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "warnings": decision.warnings,
    }


if __name__ == "__main__":
    raise SystemExit(main())

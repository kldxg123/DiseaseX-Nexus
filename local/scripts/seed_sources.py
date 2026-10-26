from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
if str(PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGES_DIR))

from common.config import get_settings
from common.db import get_session, init_db
from crawler_agent.default_sources import get_default_seed_sources
from crawler_agent.source_registry import SourceRegistryService


def _safe_source_summary(source: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(source["name"]),
        "status": str(source["status"]),
    }


def seed_sources(database_url: str) -> dict[str, Any]:
    init_db(database_url)
    seeds = get_default_seed_sources()
    created = 0
    updated = 0
    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        for seed in seeds:
            existing = service.get_source_by_base_url(seed["base_url"])
            service.upsert_source(**seed)
            if existing is None:
                created += 1
            else:
                updated += 1
    return {
        "seed_source_count": len(seeds),
        "created": created,
        "updated": updated,
        "sources": [_safe_source_summary(seed) for seed in seeds],
    }


def dry_run_summary() -> dict[str, Any]:
    seeds = get_default_seed_sources()
    return {
        "dry_run": True,
        "seed_source_count": len(seeds),
        "created": len(seeds),
        "updated": 0,
        "sources": [_safe_source_summary(seed) for seed in seeds],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed local source registry metadata.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    summary = dry_run_summary() if args.dry_run else seed_sources(
        args.database_url or get_settings().database_url
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

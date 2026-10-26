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
from crawler_agent.connector_ingest import (
    ingest_connector_payload,
    parse_connector_payload,
    store_connector_documents,
    upsert_connector_source,
)
from crawler_agent.connectors import get_connector_spec, list_official_connectors
from crawler_agent.live_fetch_guard import LiveFetchGuard
from crawler_agent.raw_document_store import RawDocumentStore

POLICY_SOURCE_KEYS = {
    "who_disease_outbreak_news": "who_don",
}


def _connector_choices() -> list[str]:
    return [spec.name for spec in list_official_connectors()]


def _dry_run_summary(connector_name: str, document_count: int, titles: list[str | None]) -> dict:
    return {
        "dry_run": True,
        "connector": connector_name,
        "mode": "fixture",
        "document_count": document_count,
        "parsed": document_count,
        "saved": 0,
        "duplicates": 0,
        "guard_allowed": None,
        "warnings": [],
        "titles": [title for title in titles if title],
    }


def _write_summary(connector_name: str, created_count: int, existing_count: int) -> dict:
    return {
        "dry_run": False,
        "connector": connector_name,
        "mode": "fixture",
        "created_count": created_count,
        "existing_count": existing_count,
        "document_count": created_count + existing_count,
        "parsed": created_count + existing_count,
        "saved": created_count,
        "duplicates": existing_count,
        "guard_allowed": None,
        "warnings": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse an official connector payload from a local fixture.",
    )
    parser.add_argument("--connector", required=True, choices=_connector_choices())
    parser.add_argument("--fixture-path", default=None)
    parser.add_argument("--fixture", dest="fixture_path", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--live-url", default=None)
    parser.add_argument("--source-policy", default="configs/source_access_policy.example.json")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--robots-allowed", action="store_true")
    parser.add_argument("--max-requests", type=int, default=1)
    args = parser.parse_args(argv)

    if not args.fixture_path:
        if args.allow_network and args.connector == "cdc_norostat" and args.live_url:
            return _run_cdc_norostat_live(args)
        if args.allow_network:
            spec = get_connector_spec(args.connector)
            source_key = POLICY_SOURCE_KEYS.get(args.connector, args.connector)
            guard = LiveFetchGuard(load_access_policies(args.source_policy))
            decision = guard.check(
                source_key,
                allow_network=args.allow_network,
                requested_url=args.base_url or spec.base_url,
                robots_allowed=True if args.robots_allowed else None,
                terms_reviewed=args.terms_reviewed,
                manual_reviewed=args.manual_reviewed,
                request_count=int(args.max_requests),
            )
            print(json.dumps({"decision": _decision_summary(decision)}, ensure_ascii=False, sort_keys=True))
            if not decision.allowed:
                raise SystemExit("Live official connector access denied by source access policy.")
            raise SystemExit("Live fetch not implemented/enabled in this MVP.")
        raise SystemExit("Refusing live official connector parsing without a local fixture.")

    spec = get_connector_spec(args.connector)
    base_url = args.base_url or spec.base_url
    payload = Path(args.fixture_path).read_text(encoding="utf-8")

    if args.dry_run:
        documents = parse_connector_payload(args.connector, payload, base_url=base_url)
        print(
            json.dumps(
                _dry_run_summary(
                    args.connector,
                    len(documents),
                    [document.title for document in documents],
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        results = ingest_connector_payload(
            session,
            args.connector,
            payload,
            base_url=base_url,
        )
    created_count = sum(1 for result in results if result.created)
    existing_count = len(results) - created_count
    print(
        json.dumps(
            _write_summary(args.connector, created_count, existing_count),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run_cdc_norostat_live(args) -> int:
    spec = get_connector_spec("cdc_norostat")
    connector = spec.connector
    try:
        documents = connector.fetch_live(
            url=args.live_url,
            allow_network=args.allow_network,
            access_guard=LiveFetchGuard(load_access_policies(args.source_policy)),
            robots_allowed=True if args.robots_allowed else None,
            terms_reviewed=args.terms_reviewed,
            manual_reviewed=args.manual_reviewed,
            request_count=int(args.max_requests),
        )
    except PermissionError as exc:
        print(
            json.dumps(
                _live_summary(
                    connector="cdc_norostat",
                    parsed=0,
                    saved=0,
                    duplicates=0,
                    guard_allowed=False,
                    warnings=[_safe_text(str(exc))],
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    if args.dry_run:
        print(
            json.dumps(
                _live_summary(
                    connector="cdc_norostat",
                    parsed=len(documents),
                    saved=0,
                    duplicates=0,
                    guard_allowed=True,
                    warnings=[],
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        source = upsert_connector_source(
            session,
            "cdc_norostat",
            base_url=args.live_url or spec.base_url,
            status="pending_review",
        )
        results = store_connector_documents(
            source=source,
            documents=documents,
            store=RawDocumentStore(session),
        )
    saved = sum(1 for result in results if result.created)
    print(
        json.dumps(
            _live_summary(
                connector="cdc_norostat",
                parsed=len(documents),
                saved=saved,
                duplicates=len(results) - saved,
                guard_allowed=True,
                warnings=[],
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _decision_summary(decision) -> dict:
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "warnings": decision.warnings,
    }


def _live_summary(
    *,
    connector: str,
    parsed: int,
    saved: int,
    duplicates: int,
    guard_allowed: bool,
    warnings: list[str],
) -> dict:
    return {
        "connector": connector,
        "mode": "live",
        "parsed": parsed,
        "saved": saved,
        "duplicates": duplicates,
        "guard_allowed": guard_allowed,
        "warnings": [_safe_text(warning) for warning in warnings],
    }


def _safe_text(value: str) -> str:
    text = str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "private key", "ssh_key_path", "key")):
        return "[redacted]"
    return text[:240]


if __name__ == "__main__":
    raise SystemExit(main())

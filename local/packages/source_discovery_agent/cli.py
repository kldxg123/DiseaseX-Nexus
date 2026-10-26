from __future__ import annotations

import argparse
import json
from typing import Any

from common.db import get_session, init_db
from source_discovery_agent.discovery_agent import SourceDiscoveryAgent
from source_discovery_agent.report import write_discovery_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local fixture-only source discovery.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover-fixture", help="Discover candidate sources from a local fixture.")
    discover.add_argument("--database-url", default=None)
    discover.add_argument("--country", required=True)
    discover.add_argument("--disease", required=True)
    discover.add_argument("--fixture", required=True)
    discover.add_argument("--output-dir", default="reports/source_discovery")
    discover.add_argument("--min-source-score", type=float, default=0.35)
    discover.add_argument("--no-connector-specs", action="store_true")
    discover.add_argument("--allow-network", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "discover-fixture":
        if args.allow_network:
            _print_json(
                {
                    "status": "failed",
                    "error": "live search not implemented or enabled in this MVP",
                    "network_accessed": False,
                }
            )
            return 2
        try:
            init_db(args.database_url).dispose()
            with get_session(args.database_url) as session:
                report = SourceDiscoveryAgent(session).discover_from_fixture(
                    country=args.country,
                    disease=args.disease,
                    fixture_path=args.fixture,
                    min_source_score=args.min_source_score,
                    generate_connector_specs=not args.no_connector_specs,
                )
                files = write_discovery_report(report, output_dir=args.output_dir)
            _print_json(
                {
                    "status": report.status,
                    "search_results_seen": report.search_results_seen,
                    "candidates_found": report.candidates_found,
                    "candidates_inserted_or_updated": report.candidates_inserted_or_updated,
                    "connector_specs_generated": report.connector_specs_generated,
                    "report_json": files.get("report_json"),
                    "report_markdown": files.get("report_markdown"),
                    "network_accessed": False,
                }
            )
            return 0
        except Exception as exc:
            _print_json({"status": "failed", "error": _safe_error(exc), "network_accessed": False})
            return 1
    _print_json({"status": "failed", "error": "unknown command", "network_accessed": False})
    return 2


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(_sanitize(payload), ensure_ascii=False, sort_keys=True))


def _safe_error(exc: BaseException) -> str:
    return _sanitize(f"{type(exc).__name__}: {exc}")[:240]


def _sanitize(value: Any) -> Any:
    sensitive = ("secret", "token", "password", "credential", "private", "ssh", "server", "username")
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in sensitive)
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    if any(part in text.lower() for part in sensitive):
        return "[redacted]"
    return text if len(text) <= 260 else text[:260] + "...[truncated]"


if __name__ == "__main__":
    raise SystemExit(main())

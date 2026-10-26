from __future__ import annotations

import argparse
import json
from typing import Any

from common.db import get_session, init_db
from data_quality_agent.label_audit import audit_model_feature_labels
from data_quality_agent.profile import build_data_quality_profile
from data_quality_agent.report import (
    build_data_quality_report,
    data_quality_profile_to_dict,
    label_audit_to_dict,
    write_report_files,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build local data-quality reports without network access.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="Profile local database tables.")
    _add_common_args(profile_parser)

    audit_parser = subparsers.add_parser("label-audit", help="Audit model feature labels.")
    _add_common_args(audit_parser)
    audit_parser.add_argument("--country", default=None)
    audit_parser.add_argument("--disease", default=None)

    report_parser = subparsers.add_parser("report", help="Build JSON and Markdown data-quality reports.")
    _add_common_args(report_parser)
    report_parser.add_argument("--country", default=None)
    report_parser.add_argument("--disease", default=None)
    report_parser.add_argument("--output-dir", default="reports")

    args = parser.parse_args(argv)
    try:
        init_db(args.database_url).dispose()
        if args.command == "profile":
            with get_session(args.database_url) as session:
                payload = data_quality_profile_to_dict(build_data_quality_profile(session))
            return _print_json({"status": "ok", "profile": payload})
        if args.command == "label-audit":
            with get_session(args.database_url) as session:
                payload = label_audit_to_dict(
                    audit_model_feature_labels(session, country=args.country, disease=args.disease)
                )
            return _print_json({"status": "ok", "label_audit": payload})
        if args.command == "report":
            with get_session(args.database_url) as session:
                report = build_data_quality_report(session, country=args.country, disease=args.disease)
            files = write_report_files(report, output_dir=args.output_dir)
            return _print_json(
                {
                    "status": "ok",
                    "report_json": files["report_json"],
                    "report_markdown": files["report_markdown"],
                    "warnings_count": files["warnings_count"],
                    "key_counts": files["key_counts"],
                }
            )
    except Exception as exc:
        return _print_json({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, exit_code=1)
    return _print_json({"status": "failed", "error": "unknown command"}, exit_code=2)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-url", default=None)


def _print_json(payload: dict[str, Any], *, exit_code: int = 0) -> int:
    print(json.dumps(_sanitize(payload), ensure_ascii=False, sort_keys=True))
    return exit_code


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in ("secret", "token", "password", "key", "credential", "server")):
                safe[key_text] = "***"
            else:
                safe[key_text] = _sanitize(item)
        return safe
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())

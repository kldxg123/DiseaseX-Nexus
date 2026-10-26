from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.data_acquisition.common import load_yaml, write_report_pair


REQUIRED_SOURCE_FIELDS = {
    "group",
    "source_type",
    "license",
    "max_file_mb",
    "allowed_roles",
    "expected_granularity",
    "expected_fields",
    "manual_semantic_review_required",
}


def validate_source_registry(path: str | Path = "configs/data_source_registry_v3.yaml") -> dict[str, Any]:
    registry = load_yaml(path)
    problems: list[dict[str, str]] = []
    scopes = registry.get("outbreak_scopes", {})
    sources = registry.get("sources", {})
    if not scopes:
        problems.append({"item": "outbreak_scopes", "problem": "missing"})
    if not sources:
        problems.append({"item": "sources", "problem": "missing"})
    for scope, item in scopes.items():
        for field in ["countries", "date_start", "date_end", "target_role"]:
            if field not in item:
                problems.append({"item": f"outbreak_scopes.{scope}", "problem": f"missing_{field}"})
    for name, item in sources.items():
        missing = sorted(field for field in REQUIRED_SOURCE_FIELDS if field not in item)
        for field in missing:
            problems.append({"item": f"sources.{name}", "problem": f"missing_{field}"})
        if "url" not in item and "url_template" not in item and "search_terms" not in item:
            problems.append({"item": f"sources.{name}", "problem": "missing_url_or_search_terms"})
    payload = {
        "status": "blocked" if problems else "ok",
        "blocked_reason": "registry_validation_failed" if problems else "none",
        "config_path": str(path),
        "scope_count": len(scopes) if isinstance(scopes, dict) else 0,
        "source_count": len(sources) if isinstance(sources, dict) else 0,
        "problems": problems,
    }
    lines = [
        "# P1b Source Registry Validation",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"scope_count: {payload['scope_count']}",
        f"source_count: {payload['source_count']}",
        "",
        "## Problems",
        "```json",
        json.dumps(problems, ensure_ascii=False, indent=2),
        "```",
    ]
    write_report_pair("p1b_source_registry_report", "P1b Source Registry Validation", payload, lines)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate P1b source registry.")
    parser.add_argument("--config", default="configs/data_source_registry_v3.yaml")
    args = parser.parse_args()
    payload = validate_source_registry(args.config)
    print(json.dumps({"status": payload["status"], "blocked_reason": payload["blocked_reason"]}, ensure_ascii=False))
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from source_discovery_agent.types import DiscoveryReport

SENSITIVE_KEY_PARTS = ("secret", "token", "password", "credential", "private", "ssh", "server", "username")
LONG_TEXT_KEYS = ("html", "raw_text", "body_text", "metadata_json", "provenance_json", "feature_json", "lesson_json")


def discovery_report_to_dict(report: DiscoveryReport) -> dict:
    return _sanitize(asdict(report))


def render_discovery_report_markdown(report: DiscoveryReport) -> str:
    payload = discovery_report_to_dict(report)
    lines = [
        "# Source Discovery Report",
        "",
        "## Summary",
        f"- Status: {payload['status']}",
        f"- Search results seen: {payload['search_results_seen']}",
        f"- Candidates found: {payload['candidates_found']}",
        f"- Inserted or updated as pending_review: {payload['candidates_inserted_or_updated']}",
        f"- Skipped below score threshold: {payload['skipped']}",
        f"- ConnectorSpec suggestions: {payload['connector_specs_generated']}",
        "",
        "## Country / Disease",
        f"- Country: {payload.get('country')}",
        f"- Disease: {payload.get('disease')}",
        "",
        "## Queries",
    ]
    for query in payload.get("queries", []):
        lines.append(f"- {query.get('query_text')}")
    lines.extend(["", "## Candidates"])
    for candidate in payload.get("top_candidates", []):
        lines.extend(
            [
                f"- {candidate.get('name')} ({candidate.get('domain')})",
                f"  - Base URL: {candidate.get('base_url')}",
                f"  - Score: {candidate.get('source_score')}",
                f"  - Type: {candidate.get('source_type')}",
                f"  - Status: {candidate.get('status')}",
                f"  - Requires human review: {candidate.get('requires_human_review')}",
            ]
        )
    lines.extend(["", "## Inserted Pending Review Sources"])
    lines.append(
        "All saved candidate sources are stored in `source_registry` with `pending_review`; "
        "no source is automatically activated."
    )
    lines.extend(["", "## ConnectorSpec Suggestions"])
    for candidate in payload.get("top_candidates", []):
        spec = candidate.get("connector_spec")
        if not isinstance(spec, dict):
            continue
        lines.extend(
            [
                f"- {spec.get('connector_name')}",
                f"  - Source: {spec.get('source_name')}",
                f"  - Parser strategy: {spec.get('parser_strategy')}",
                f"  - Requires human review: {spec.get('requires_human_review')}",
                f"  - Test fixture required: {spec.get('test_fixture_required')}",
            ]
        )
    if not any(isinstance(candidate.get("connector_spec"), dict) for candidate in payload.get("top_candidates", [])):
        lines.append("- None generated.")
    lines.extend(["", "## Warnings"])
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    lines.extend(
        [
            "",
            "## Human Review Required",
            "Every discovered source and ConnectorSpec suggestion requires analyst review before any live access, activation, or connector implementation.",
            "",
            "## Safety Notes",
            "- This MVP reads local fixture/mock search results only.",
            "- No live search, CDC/WHO/ECDC/GDELT request, search-engine request, SSH, Docker, or remote deployment is executed.",
            "- Reports do not include full page bodies, raw payloads, or internal long-form JSON payloads.",
            "- Live discovery must be added later through Live Access Guardrails and explicit human approval.",
            "",
        ]
    )
    return "\n".join(lines)


def write_discovery_report(
    report: DiscoveryReport,
    *,
    output_dir: str = "reports/source_discovery",
    prefix: str = "source_discovery",
) -> dict:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = _safe_filename(prefix)
    json_path = target_dir / f"{safe_prefix}_{timestamp}.json"
    markdown_path = target_dir / f"{safe_prefix}_{timestamp}.md"
    json_path.write_text(
        json.dumps(discovery_report_to_dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_discovery_report_markdown(report), encoding="utf-8")
    return {
        "report_json": str(json_path),
        "report_markdown": str(markdown_path),
        "candidates_found": report.candidates_found,
        "candidates_inserted_or_updated": report.candidates_inserted_or_updated,
        "connector_specs_generated": report.connector_specs_generated,
    }


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS + LONG_TEXT_KEYS):
                continue
            result[key_text] = _sanitize(item)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:50]]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    lowered_text = text.lower()
    if any(part in lowered_text for part in SENSITIVE_KEY_PARTS):
        return "[redacted]"
    if len(text) > 260:
        return text[:260] + "...[truncated]"
    return text


def _safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned.strip("_") or "source_discovery"

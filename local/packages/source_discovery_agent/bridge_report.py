from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from source_discovery_agent.generic_bridge import (
    DiscoveryParserBridgeResult,
    discovery_parser_bridge_result_to_dict,
)


def bridge_result_to_dict(result: DiscoveryParserBridgeResult) -> dict:
    return discovery_parser_bridge_result_to_dict(result)


def render_bridge_report_markdown(results: list[DiscoveryParserBridgeResult]) -> str:
    payloads = [bridge_result_to_dict(result) for result in results]
    total_raw = sum(int(item.get("raw_documents_saved", 0)) for item in payloads)
    total_events = sum(int(item.get("epidemic_events_created", 0)) for item in payloads)
    total_features = sum(int(item.get("model_features_created", 0)) for item in payloads)
    lines = [
        "# Source Discovery to Generic Parser Bridge Report",
        "",
        "## Summary",
        f"- Strategies tested: {len(payloads)}",
        f"- Raw documents saved: {total_raw}",
        f"- Epidemic events created: {total_events}",
        f"- Model features created: {total_features}",
        "",
        "## Parser Strategies Tested",
    ]
    for item in payloads:
        lines.extend(
            [
                f"- {item.get('parser_strategy')}",
                f"  - Status: {item.get('status')}",
                f"  - Parsed records: {item.get('parsed_records')}",
                f"  - Connector documents: {item.get('connector_documents')}",
                f"  - Raw documents saved: {item.get('raw_documents_saved')}",
                f"  - Epidemic events created: {item.get('epidemic_events_created')}",
                f"  - Model features created: {item.get('model_features_created')}",
            ]
        )
    lines.extend(["", "## Warnings"])
    warnings = [warning for item in payloads for warning in item.get("warnings", [])]
    if warnings:
        for warning in warnings[:40]:
            lines.append(f"- {warning}")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Safety Notes",
            "- This bridge used local fixtures only.",
            "- No live search, crawler fetch, SSH, Docker, or remote deployment was executed.",
            "- Candidate sources remain pending_review.",
            "- Reports omit full fixture payloads, raw text, complete metadata, provenance, features, and lessons.",
            "",
            "## Human Review Required",
            "Analyst review is required before any source activation, live connector use, or production workflow.",
            "",
        ]
    )
    return "\n".join(lines)


def write_bridge_report(
    results: list[DiscoveryParserBridgeResult],
    *,
    output_dir: str = "reports/source_discovery",
    prefix: str = "discovery_parser_bridge",
) -> dict:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = _safe_filename(prefix)
    json_path = target_dir / f"{safe_prefix}_{timestamp}.json"
    markdown_path = target_dir / f"{safe_prefix}_{timestamp}.md"
    payload = {
        "results": [bridge_result_to_dict(result) for result in results],
        "network_accessed": False,
        "human_review_required": True,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_bridge_report_markdown(results), encoding="utf-8")
    return {
        "report_json": str(json_path),
        "report_markdown": str(markdown_path),
        "strategies_tested": [result.parser_strategy for result in results],
        "raw_documents_saved": sum(result.raw_documents_saved for result in results),
        "epidemic_events_created": sum(result.epidemic_events_created for result in results),
        "model_features_created": sum(result.model_features_created for result in results),
    }


def _safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned.strip("_") or "discovery_parser_bridge"

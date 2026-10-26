from __future__ import annotations

import re
from urllib.parse import urlsplit

from crawler_optimizer_agent.types import ConnectorSpec, safe_text, safe_url

EXPECTED_FIELDS = [
    "title",
    "url",
    "published_at",
    "text",
    "country_detected",
    "disease_detected",
]


def slugify_connector_name(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "connector"


def infer_connector_spec_from_source(source, *, sample_urls: list[str] | None = None) -> ConnectorSpec:
    source_name = str(getattr(source, "name", None) or "candidate_source")
    metadata = getattr(source, "metadata_json", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    parser_strategy = _infer_parser_strategy(source)
    urls = [safe_url(url) for url in (sample_urls or []) if safe_url(url)]
    if not urls and getattr(source, "base_url", None):
        urls = [safe_url(getattr(source, "base_url"))]
    return ConnectorSpec(
        connector_name=slugify_connector_name(source_name),
        source_name=source_name,
        base_url=str(getattr(source, "base_url", "") or ""),
        source_type=str(getattr(source, "source_type", "") or "unknown"),
        parser_strategy=parser_strategy,
        expected_fields=list(EXPECTED_FIELDS),
        sample_urls=urls[:3],
        robots_required=True,
        rate_limit_seconds=_rate_limit(source),
        requires_human_review=True,
        test_fixture_required=True,
        live_fetch_supported=bool(metadata.get("live_fetch_supported", False)),
        notes="Generated connector specification draft. Requires human review before implementation.",
    )


def connector_spec_to_markdown(spec: ConnectorSpec) -> str:
    data = spec.to_dict()
    sample_urls = "\n".join(f"- {url}" for url in data["sample_urls"]) or "- n/a"
    expected_fields = ", ".join(data["expected_fields"])
    return "\n".join(
        [
            f"# Connector Spec: {data['connector_name']}",
            "",
            f"- Source: {data['source_name']}",
            f"- Base URL: {data['base_url']}",
            f"- Source type: {data['source_type']}",
            f"- Parser strategy: {data['parser_strategy']}",
            f"- Expected fields: {expected_fields}",
            f"- Robots required: {data['robots_required']}",
            f"- Rate limit seconds: {data['rate_limit_seconds']}",
            f"- Requires human review: {data['requires_human_review']}",
            f"- Test fixture required: {data['test_fixture_required']}",
            f"- Live fetch supported: {data.get('live_fetch_supported', False)}",
            "",
            "## Sample URLs",
            sample_urls,
            "",
            f"Notes: {safe_text(data.get('notes'))}",
        ]
    )


def safe_connector_spec_summary(spec: ConnectorSpec) -> dict:
    return spec.to_dict()


def _infer_parser_strategy(source) -> str:
    parser_type = str(getattr(source, "parser_type", "") or "").lower()
    source_type = str(getattr(source, "source_type", "") or "").lower()
    metadata = getattr(source, "metadata_json", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    base_url = str(getattr(source, "base_url", "") or "").lower()
    path = urlsplit(base_url).path
    if "csv" in parser_type or path.endswith(".csv"):
        return "csv_dataset"
    if "json" in parser_type:
        return "json_api"
    if "dataset" in source_type:
        return "official_dataset"
    if metadata.get("table") or "table" in parser_type:
        return "html_table"
    if "news" in source_type or "article" in parser_type:
        return "news_article"
    return parser_type if parser_type and parser_type != "metadata_only" else "generic_html"


def _rate_limit(source) -> float:
    metadata = getattr(source, "metadata_json", None)
    if isinstance(metadata, dict):
        try:
            return max(1.0, float(metadata.get("rate_limit_seconds", 2.0)))
        except (TypeError, ValueError):
            pass
    return 2.0

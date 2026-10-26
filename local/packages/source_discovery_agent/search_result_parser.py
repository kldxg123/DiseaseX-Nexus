from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crawler_agent.url_utils import normalize_url
from source_discovery_agent.types import SearchResult

MAX_SAFE_SNIPPET_CHARS = 220


def parse_search_results_fixture(data: dict | list) -> list[SearchResult]:
    rows = _extract_rows(data)
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        try:
            normalized_url = normalize_url(str(row.get("url") or ""))
        except ValueError:
            continue
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        results.append(
            SearchResult(
                title=_optional_text(row.get("title")),
                url=normalized_url,
                snippet=_truncate(_optional_text(row.get("snippet"))),
                source_name=_optional_text(row.get("source_name")),
                rank=_optional_int(row.get("rank"), default=index),
                published_at=_optional_text(row.get("published_at")),
                metadata={
                    "fixture_rank": index,
                    "parser": "source_discovery_fixture",
                },
            )
        )
    return results


def load_search_results_fixture(path: str) -> list[SearchResult]:
    fixture_path = Path(path).expanduser()
    with fixture_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, (dict, list)):
        raise ValueError("search results fixture must be a JSON object or array.")
    return parse_search_results_fixture(data)


def _extract_rows(data: dict | list) -> list[Any]:
    if isinstance(data, list):
        return data
    results = data.get("results")
    if isinstance(results, list):
        return results
    queries = data.get("queries")
    if isinstance(queries, list):
        rows: list[Any] = []
        for query in queries:
            if isinstance(query, dict) and isinstance(query.get("results"), list):
                rows.extend(query["results"])
        return rows
    return []


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= MAX_SAFE_SNIPPET_CHARS:
        return value
    return value[:MAX_SAFE_SNIPPET_CHARS] + "...[truncated]"

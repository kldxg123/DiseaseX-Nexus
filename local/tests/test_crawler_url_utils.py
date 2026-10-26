from __future__ import annotations

import pytest

from crawler_agent.url_utils import normalize_url


def test_normalize_url_lowercases_host_sorts_query_and_removes_fragment() -> None:
    assert (
        normalize_url("HTTPS://Example.Invalid:443/path?b=2&a=1#section")
        == "https://example.invalid/path?a=1&b=2"
    )


def test_normalize_url_resolves_relative_url() -> None:
    assert (
        normalize_url("../report", base_url="https://example.invalid/news/index.html")
        == "https://example.invalid/report"
    )


def test_normalize_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError):
        normalize_url("file:///tmp/local.html")

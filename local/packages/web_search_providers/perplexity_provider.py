from __future__ import annotations

from web_search_providers.skeleton_provider import SkeletonWebSearchProvider


class PerplexityWebSearchProvider(SkeletonWebSearchProvider):
    def __init__(self) -> None:
        super().__init__("perplexity", native_web_search=True, citations=True)

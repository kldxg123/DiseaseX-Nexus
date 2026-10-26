from __future__ import annotations

from web_search_providers.skeleton_provider import SkeletonWebSearchProvider


class SerpApiWebSearchProvider(SkeletonWebSearchProvider):
    def __init__(self) -> None:
        super().__init__("serpapi")

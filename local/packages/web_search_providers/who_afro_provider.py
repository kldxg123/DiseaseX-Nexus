from __future__ import annotations

from web_search_providers.skeleton_provider import SkeletonWebSearchProvider


class WhoAfroSearchProvider(SkeletonWebSearchProvider):
    def __init__(self) -> None:
        super().__init__("who_afro")

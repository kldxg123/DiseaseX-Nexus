from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RobotsPolicy(Protocol):
    def allowed(self, url: str, user_agent: str) -> bool:
        ...


@dataclass(frozen=True)
class StaticRobotsPolicy:
    is_allowed: bool = True

    def allowed(self, url: str, user_agent: str) -> bool:
        return self.is_allowed

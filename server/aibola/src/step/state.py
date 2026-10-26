from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepState:
    tasks_run: list[str] = field(default_factory=list)
    stimuli: list[dict] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)

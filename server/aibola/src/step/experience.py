from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.utils.time import utc_now_iso


@dataclass
class Experience:
    experience_id: str
    date: str
    country: str
    adm1: str = ""
    horizon_days: int = 14
    features: dict[str, Any] = field(default_factory=dict)
    prediction: dict[str, Any] = field(default_factory=dict)
    true_label: str | None = None
    label_available_at: str | None = None
    model_version: str = "baseline_v1"
    adapter_version: str = "none"
    source_provenance: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    human_feedback: str | None = None
    outbreak: str | None = None
    data_completeness: str = "unknown"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

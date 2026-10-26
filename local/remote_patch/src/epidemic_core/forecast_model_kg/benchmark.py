"""Read-only, versioned forecast benchmark artifacts for the KG UI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_sgp_dengue_paper_benchmark() -> dict[str, Any]:
    """Load the immutable, paper-aligned Singapore dengue replay artifact.

    This is intentionally separate from the fresh held-out registry. It is a
    read-only retrospective benchmark and must never be presented as a new
    blind-window certification.
    """
    path = Path(__file__).resolve().parent / "reference" / "sgp_dengue_paper_benchmark.v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["payload_sha256"] = _hash_payload(payload)
    return payload

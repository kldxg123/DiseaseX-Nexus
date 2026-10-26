# ruff: noqa: E501  # long lines hold verbatim paper excerpts / hashes / Chinese UI strings
"""Read-only loader for the formal top-venue paper benchmark artifact.

Bleichrodt et al. 2024 (Royal Society Open Science 11(7):240248,
DOI 10.1098/rsos.240248) method-transfer benchmark on the Singapore dengue
weekly series. SHADOW_ONLY: never promoted, never touches Trusted data or
Active Pointers. This is the primary formal-paper benchmark; the legacy
arXiv-preprint benchmark (sgp_dengue_paper_benchmark.v1.json) is retained
unchanged as historical evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_rsos_mpox_paper_benchmark() -> dict[str, Any]:
    """Load the immutable formal-paper method-transfer benchmark artifact.

    Read-only retrospective benchmark (METHOD_TRANSFER_REPLICATION); it must
    never be presented as a fresh blind-window certification.
    """
    path = Path(__file__).resolve().parent / "reference" / "mpox_rsos2024_paper_benchmark.v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["payload_sha256"] = _hash_payload(payload)
    return payload

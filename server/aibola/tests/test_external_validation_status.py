from __future__ import annotations

from src.reports.external_validation_status import build_p0e_status


def test_external_validation_status_importable() -> None:
    assert callable(build_p0e_status)

from __future__ import annotations


class EpidemicCoreError(Exception):
    """Base error for the domain layer."""


class ResolutionError(EpidemicCoreError):
    def __init__(self, code: str, candidates: tuple[str, ...] = ()) -> None:
        self.code = code
        self.candidates = candidates[:5]
        super().__init__(code)


class ArtifactIntegrityError(EpidemicCoreError):
    """Raised when content-addressed bytes do not match their digest."""

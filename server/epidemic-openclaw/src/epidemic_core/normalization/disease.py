from __future__ import annotations

from collections.abc import Iterable

from epidemic_core.domain.query import DiseaseResolution
from epidemic_core.errors import ResolutionError
from epidemic_core.normalization.text import normalize_alias


class DiseaseResolver:
    def __init__(self, rows: Iterable[tuple[str, str]]) -> None:
        aliases: dict[str, set[str]] = {}
        for alias, slug in rows:
            aliases.setdefault(normalize_alias(alias), set()).add(slug)
        self._aliases = aliases

    def resolve(self, raw: str) -> DiseaseResolution:
        key = normalize_alias(raw)
        matches = tuple(sorted(self._aliases.get(key, ())))
        if len(matches) > 1:
            raise ResolutionError("AMBIGUOUS", matches)
        if not matches:
            suggestions = tuple(sorted(k for k in self._aliases if key and key in k))[:5]
            raise ResolutionError("NOT_FOUND", suggestions)
        return DiseaseResolution(slug=matches[0], normalized_input=key)

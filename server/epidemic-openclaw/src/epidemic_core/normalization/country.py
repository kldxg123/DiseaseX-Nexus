from __future__ import annotations

from collections.abc import Iterable

from epidemic_core.domain.query import CountryResolution
from epidemic_core.errors import ResolutionError
from epidemic_core.normalization.text import normalize_alias


class CountryResolver:
    def __init__(self, rows: Iterable[tuple[str, str]]) -> None:
        aliases: dict[str, set[str]] = {}
        for alias, alpha2 in rows:
            aliases.setdefault(normalize_alias(alias), set()).add(alpha2)
        self._aliases = aliases

    def resolve(self, raw: str) -> CountryResolution:
        key = normalize_alias(raw)
        matches = tuple(sorted(self._aliases.get(key, ())))
        if len(matches) > 1:
            raise ResolutionError("AMBIGUOUS", matches)
        if not matches:
            suggestions = tuple(sorted(k for k in self._aliases if key and key in k))[:5]
            raise ResolutionError("NOT_FOUND", suggestions)
        return CountryResolution(alpha2=matches[0], normalized_input=key)

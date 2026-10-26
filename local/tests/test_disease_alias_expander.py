from __future__ import annotations

from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases


def test_known_disease_aliases_include_cholera_terms():
    aliases = expand_disease_aliases("cholera")
    assert "vibrio cholerae" in [item.lower() for item in aliases.aliases]
    assert aliases.unknown_disease_alias_expansion is False
    assert aliases.alias_confidence > 0.8


def test_unknown_disease_gets_safe_query_expansion():
    aliases = expand_disease_aliases("mystery fever")
    assert aliases.unknown_disease_alias_expansion is True
    assert "mystery fever surveillance" in aliases.aliases
    assert "mystery fever dataset" in aliases.aliases
    assert aliases.alias_confidence < 0.6
